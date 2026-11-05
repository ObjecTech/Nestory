const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname,'../static/assistant.js'),'utf8');
function browser(url, fetch) {
  const location = new URL(url); let redirected;
  location.assign = path => { redirected=path; };
  const context={window:{location,__TEST_ENV__:true},document:{cookie:'bmon_csrf=TEST_ONLY_CSRF',querySelector:()=>null,querySelectorAll:()=>[]},fetch,URL,URLSearchParams,console,setTimeout,clearTimeout,crypto};
  vm.runInNewContext(code,context);
  return {A:context.window.Assistant,redirected:()=>redirected};
}
test('404/501/503 are real errors; no Mock activation or false save',async()=>{
  for(const status of [404,501,503]) {
    const {A}=browser('http://127.0.0.1:18881/',async()=>({status,ok:false,json:async()=>({api_version:'1',error:{code:'real_error',message:'真实失败',retryable:false}})}));
    const before=JSON.stringify(A.mockStore);
    await assert.rejects(A.assistantApi('/api/assistant/conversations?scope=demo'),e=>e.status===status && e.code==='real_error');
    assert.equal(A.state.isMock,false); assert.equal(JSON.stringify(A.mockStore),before);
  }
});
test('disconnect reports connection failure without writing to memory',async()=>{
  const {A}=browser('http://127.0.0.1:18881/',async()=>{throw new TypeError('Failed to fetch');});
  await assert.rejects(A.assistantApi('/api/assistant/care-events',{method:'POST',body:'{}'}),e=>e.code==='network_error' && /连接失败/.test(e.message));
  assert.equal(A.state.isMock,false);
});
test('401 navigates to login and retains typed error',async()=>{
  const {A,redirected}=browser('http://127.0.0.1:18881/',async()=>({status:401,ok:false,json:async()=>({api_version:'1',error:{code:'authentication_required'}})}));
  await assert.rejects(A.assistantApi('/api/assistant/conversations?scope=demo'),e=>e.status===401 && e.code==='authentication_required');
  assert.equal(redirected(),'/login'); assert.equal(A.state.isMock,false);
});
test('every write carries same-origin credentials, CSRF and no-store',async()=>{
  const seen=[];
  const {A}=browser('http://127.0.0.1:18881/',async(path,options)=>{seen.push(options);return {status:200,ok:true,json:async()=>({api_version:'1'})};});
  for(const method of ['POST','PATCH','DELETE']) await A.assistantApi('/api/assistant/conversations',{method});
  for(const request of seen) {assert.equal(request.credentials,'same-origin');assert.equal(request.cache,'no-store');assert.equal(request.headers['X-CSRF-Token'],'TEST_ONLY_CSRF');}
});
test('Mock can only be explicitly enabled in local development',async()=>{
  const {A}=browser('https://example.com/?assistant_mock=1',async()=>{throw new Error('unexpected');});
  assert.equal(A.state.isMock,false); assert.throws(()=>A.setMock(true),/开发测试/);
  const local=browser('http://127.0.0.1:18881/?assistant_mock=1',async()=>{throw new Error('unexpected network');}).A;
  assert.equal(local.state.isMock,true);
  assert.ok((await local.assistantApi('/api/assistant/conversations?scope=demo')).conversations.length>0);
});
test('poll URLs cannot cause credential-bearing cross-origin requests',async()=>{
  let calls=0;
  const {A}=browser('http://127.0.0.1:18881/',async()=>{calls++;});
  await assert.rejects(A.assistantApi('https://example.com/api/assistant/conversations'),/地址无效/);
  assert.equal(calls,0);
});
