import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { interiorFixture } from './interior_fixture.mjs';
const fixture = await interiorFixture(), { page } = fixture;
const output = new URL('../../../design/previews/meta-removal/', import.meta.url);
await mkdir(output, { recursive: true });
try {
  await page.getByTestId('home-settings').click();
  await page.getByTestId('interior-settings-tab-models').click();
  assert.equal(await page.locator('#is-mirror-baseten').innerText(), 'Baseten account: not verified.');
  assert.equal(await page.locator('#ai-key-meta').count(), 1);
  assert.equal(await page.locator('#ai-key-meta').inputValue(), '');
  await page.evaluate(() => renderKeyStatus({providers:[{id:'meta'}],settings:{key_configured:true}}));
  assert.equal(await page.locator('#ai-key-status-meta').innerText(), 'No key saved yet.');
  await page.screenshot({path:fileURLToPath(new URL('settings-after.png',output))});
  await page.evaluate(() => WB_I18N.set('ko'));
  await page.screenshot({path:fileURLToPath(new URL('settings-after-ko.png',output))});
  await page.evaluate(() => WB_I18N.set('en'));
  await page.keyboard.press('Escape');
  await page.evaluate(async () => {
    const original = call;
    call = async (method, path) => {
      if(path==='/ai/registry')return {
        providers:[{id:'modal_shared',prerequisites_ready:true}],settings:{cloud_enabled:false,budget_usd:1,local_allowance_remaining_usd:1},
        tasks:{research_query:'meta-models/muse-spark-open-1026',literature_analysis:'meta-models/muse-spark-open-1026',methodology_review:'muse-wonder-1.0',cross_artifact_review:'muse-wonder-1.0',transcribe:'muse-voice-transcribe-1.0',segment:'sam-3.1',image_generate:'muse-image-1.0',image_edit:'muse-image-1.0'},
        models:['muse-wonder-1.0','muse-spark-1.3','muse-spark-1.3-contributor','meta-models/muse-spark-open-1026','local:llamacpp:qwen3-4b-instruct-2507-q4_k_m','modal-shared:muse-spark-open'].map(id=>({id,endpoint:'/responses',kind:id.startsWith('local:')?'local_reasoning':'reasoning'}))
      };
      return path.endsWith('/assets')?{assets:[]}:{jobs:[]};
    };
    const stale=document.createElement('option');
    stale.value='methodology_review'; stale.textContent='Methodology review';
    $('ai-task').appendChild(stale); $('ai-task').value=stale.value;
    if($('ai-task').value!=='methodology_review')throw Error('stale task must be selected before refresh');
    S.question='synthetic'; await loadAI('synthetic');
  });
  assert.equal(await page.locator('#ai-task').inputValue(), 'research_query');
  assert.deepEqual(await page.locator('#ai-task option').evaluateAll(options=>options.map(option=>option.value)),
    ['research_query','literature_analysis']);
  assert.equal(await page.evaluate(() => Array.from($('ai-task').options).every(option=>
    modelRoute(aiRegistry.tasks[option.value])!=='meta')), true, 'no offered task resolves to a hidden Meta model');
  assert.deepEqual(await page.locator('#ai-model option').evaluateAll(els=>els.map(el=>el.value)),
    ['auto','meta-models/muse-spark-open-1026','local:llamacpp:qwen3-4b-instruct-2507-q4_k_m','modal-shared:muse-spark-open']);
  const english = await page.locator('#ai-model').innerHTML();
  assert.match(english, /Muse Spark Open \(Baseten\) \/ baseten \/ account not verified/);
  assert.match(english, /Qwen3 4B Instruct \(local\) \/ local/);
  assert.match(english, /Muse Spark Open \(Modal Shared\) \/ modal_shared/);
  await page.evaluate(async () => { WB_I18N.set('ko'); await loadAI('synthetic'); });
  const korean = await page.locator('#ai-model').innerHTML();
  assert.match(korean, /Muse Spark Open \(Modal 공유\) \/ modal_shared/);
  for (const [suffix, options] of [['',english],['-ko',korean]]) {
    const capture = await page.context().browser().newPage();
    try {
      await capture.setContent(`<html><body style="margin:24px;background:#f5f5f7"><select aria-label="Model" size="3" style="width:900px;font:20px system-ui;padding:12px">${options}</select></body></html>`);
      await capture.locator('select').screenshot({path:fileURLToPath(new URL(`picker-after${suffix}.png`,output))});
    } finally { await capture.close(); }
  }
  console.log('META_CHOICES_PASS: executable Shared offered in English/Korean, Meta hidden, one empty Meta key, stored presence hidden');
} finally { await fixture.close(); }
