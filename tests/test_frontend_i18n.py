"""Exercise shipped locale selection and real event/API paths without models."""

from pathlib import Path
import shutil
import subprocess
import unittest

from test_frontend_events import APP, NODE_FIXTURE
from test_xray_frontend import XRAY_CASES


LOCALE_FIXTURE = NODE_FIXTURE[:NODE_FIXTURE.index("const cases = {")]
LOCALE_FIXTURE = LOCALE_FIXTURE.replace(
    "function setup({ observer = false, hover = false, pilot = false, config = {} } = {}) {",
    "function setup({ observer = false, hover = false, pilot = false, config = {}, languages = ['en-US'], "
    "stored = null, brokenStorage = false } = {}) {",
).replace(
    "  vm.runInContext(source.replace(marker,",
    """  document.documentElement = { lang: '', dataset: {} };
  document.querySelectorAll = () => [];
  document.createTreeWalker = () => {
    let index = -1;
    return { nextNode() { index++; this.currentNode = (document.textNodes || [])[index]; return Boolean(this.currentNode); } };
  };
  context.NodeFilter = { SHOW_TEXT: 4 };
  const localeStore = new Map(stored ? [['norn.language.v1', stored]] : []);
  context.window.navigator = { languages, language: languages[0] };
  context.window.localStorage = {
    getItem(key) { if (brokenStorage) throw Error('storage denied'); return localeStore.get(key); },
    setItem(key, value) { if (brokenStorage) throw Error('storage denied'); localeStore.set(key, value); },
  };
  vm.runInContext(fs.readFileSync(require('node:path').join(require('node:path').dirname(process.argv[1]), 'i18n.js'), 'utf8'), context);
  vm.runInContext(source.replace(marker,""",
).replace(
    "return { ...api, requests,",
    "return { ...api, i18n: context.window.NornI18n, localeStore, domDocument: document, requests,",
)

LOCALE_FIXTURE += XRAY_CASES[:XRAY_CASES.index("async function closedPanelLiveRefetchAndLocalScrubbing")]
LOCALE_FIXTURE += r"""
async function localeSelection() {
  for (const [languages, expected] of [
    [['en-US', 'zh-CN'], 'en'], [['zh-CN', 'en'], 'zh'], [['zh-TW'], 'zh'], [['fr-FR'], 'en'],
  ]) assert.equal(setup({ languages }).i18n.language, expected);
  assert.equal(setup({ languages: ['en-US'], stored: 'zh' }).i18n.language, 'zh');
  const denied = setup({ languages: ['zh-TW'], brokenStorage: true });
  denied.i18n.setLanguage('en');
  assert.equal(denied.i18n.language, 'en', 'Storage failure must not prevent a manual change');
  const app = setup();
  assert.equal(app.i18n.t('下一轮 →'), 'Next round →');
  app.i18n.setLanguage('zh');
  assert.equal(app.localeStore.get('norn.language.v1'), 'zh');
  assert.equal(app.i18n.t('下一轮 →'), '下一轮 →');
  app.i18n.setLanguage('en');
  const result = app.i18n.t(['选了 ', ' / ', ' 张'], 2, 6);
  assert.equal(result, 'Selected 2 of 6');
  assert.equal(app.i18n.translate(result, 'zh'), '选了 2 / 6 张');
  const label = { nodeValue: '已选 ', parentElement: { closest() { return false; } } };
  const prose = { nodeValue: '命运已经织成', parentElement: { closest() { return true; } } };
  app.domDocument.textNodes = [label, prose];
  app.i18n.apply();
  assert(label.nodeValue.includes('Selected'));
  assert.equal(prose.nodeValue, '命运已经织成', 'Authored prose is not a translatable UI label');
  app.i18n.setLanguage('zh');
  assert.equal(label.nodeValue, '已选 ', 'Static labels return to their original Chinese text');
}

async function englishWorkflow() {
  const app = setup({ languages: ['en-GB', 'zh-CN'] });
  app.drawRound(); app.paintRound(); app.dom.cardGrid.children[0].click();
  assert.equal(app.dom.cardGrid.dataset.cardCount, '4');
  app.responses.push({ status: 200, data: { ...successfulAnalysis, reading: 'Your choices leave space for a quiet discovery.',
    language: 'en', max_question_chars: 400 } });
  await app.revealDestiny();
  assert.equal(JSON.parse(app.requests[0].body).language, 'en');
  assert.equal(app.dom.dialogueInput.maxLength, 400);
  assert.equal(app.dom.dialogueCount.textContent, '0/400');
  assert(app.dom.resultMode.textContent.includes('No model calls'));
  const reading = app.state.analysisResult.reading;
  app.i18n.setLanguage('zh');
  assert.equal(app.state.analysisResult.reading, reading, 'UI language must not rewrite frozen prose');
  app.dom.dialogueInput.value = 'a'.repeat(300);
  app.responses.push({ status: 200, data: { reply: 'An English reply.', turn_count: 1, max_turns: 1,
    language: 'en', max_question_chars: 400 } });
  await app.handleDialogueSubmit({ preventDefault() {} });
  assert.equal(app.requests.length, 2, 'The English session keeps its longer question allowance after UI switching');
  const question = JSON.parse(app.requests[1].body);
  assert.equal(question.user_message.length, 300);
  assert.equal(question.language, undefined, 'Follow-ups inherit the frozen session language');
}

async function englishXrayAfterToggle() {
  const app = setup({ languages: ['zh-CN'] });
  app.i18n.setLanguage('en');
  app.initializeXray(); app.drawRound(); app.paintRound();
  app.responses.push({ status: 200, data: trajectory(1) });
  app.setXrayOpen(true); await app.runTimer(0);
  const bars = textOf(app.dom.xrayBars);
  assert(bars.includes('Introverted I') && bars.includes('Extraverted E'));
  assert(!/[\u4e00-\u9fff]/.test(bars), 'A first-open X-Ray uses the current language, not the startup language');
  assert(!/[\u4e00-\u9fff]/.test(textOf(app.dom.xrayRoundNote)), 'Movement labels also use the current language');
}

async function guardedXrayBilingualFallback() {
  const app = setup({ languages: ['en-US'] });
  app.initializeXray();
  app.responses.push({ status: 200, data: guardedTrajectory(1) });
  app.setXrayOpen(true); await app.runTimer(0);
  assert.equal(textOf(app.dom.xrayTypes).trim(), 'Not enough evidence to distinguish images yet.');
  assert(!/[\u4e00-\u9fff]/.test(textOf(app.dom.xrayRoundNote)));
  assert.equal(app.state.xrayMarker.group.style.display, 'none');
  app.dom.xrayEngineering.open = true; app.dom.xrayEngineering.listeners.toggle();
  assert(app.state.xrayDetailNodes.posterior.textContent.includes('Effective parameters'));
  assert(app.state.xrayDetailNodes.posterior.textContent.includes('candidate standard deviations'));
  assert(!/[\u4e00-\u9fff]/.test(app.state.xrayDetailNodes.parameters.textContent));
  assert(!/[\u4e00-\u9fff]/.test(app.state.xrayDetailNodes.posterior.textContent));
  assert.equal(app.i18n.translate('Not enough evidence to distinguish images yet.', 'zh'), '证据不足，暂不区分图片');
  assert.equal(app.i18n.t('生效贡献用于打分；不确定性来自未门控候选，回退不代表确定没有偏好。'),
    'Effective contributions determine scores; uncertainty belongs to the ungated candidate. Falling back does not establish an absence of taste.');
  assert.equal(app.requests.length, 1);
}

async function researchXrayBilingualLayersAndContributions() {
  const app = setup({ languages: ['en-US'], config: { deck: 'pamela-v1' } });
  app.initializeXray();
  const data = researchTrajectory();
  app.state.allCards = data.cards.map(card => ({ id: card.id, url: card.image }));
  app.responses.push({ status: 200, data });
  app.setXrayOpen(true); await app.runTimer(0);
  for (const block of [app.dom.xrayBars, app.dom.xrayTypes, app.dom.xrayRoundNote,
                       app.dom.xrayLegend, app.dom.xrayBarsNote, app.dom.xrayGuessNote,
                       app.dom.xrayComparisonHeading, app.dom.xrayComparisonMode, app.dom.xrayComparisonScope,
                       app.dom.xrayComparisonLegend, app.dom.xrayComparisonContext, app.dom.xrayComparisonCards,
                       app.dom.xrayComparisonResult, app.dom.xrayComparisonLoss, app.dom.xrayComparisonNote,
                       app.dom.xrayLossValue]) {
    assert(!/[\u4e00-\u9fff]/.test(textOf(block)), textOf(block));
  }
  assert(textOf(app.dom.xrayBars).includes('Reference taste'));
  assert(textOf(app.dom.xrayTypes).includes('not accuracy or probability'));
  app.dom.xrayRound.value = '2'; app.dom.xrayRound.listeners.input();
  assert(textOf(app.dom.xrayRoundNote).includes('activated Personal preference'));
  app.dom.xrayEngineering.open = true; app.dom.xrayEngineering.listeners.toggle();
  app.state.xrayDetailNodes.breakdown.open = true; app.state.xrayDetailNodes.breakdown.listeners.toggle();
  assert(!/[\u4e00-\u9fff]/.test(textOf(app.dom.xrayDetails)), textOf(app.dom.xrayDetails));
  assert(textOf(app.dom.xrayDetails).includes('Total'));
  app.i18n.setLanguage('zh');
  app.dom.xrayRound.value = '1'; app.dom.xrayRound.listeners.input();
  assert(textOf(app.dom.xrayBars).includes('口味敏感度'));
  assert(app.state.xrayDetailNodes.parameters.textContent.includes('口味敏感度'));
  assert.equal(app.requests.length, 1);
}

const scenarios = { localeSelection, englishWorkflow, englishXrayAfterToggle, guardedXrayBilingualFallback, researchXrayBilingualLayersAndContributions };
scenarios[process.argv[2]]().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is optional for frontend fixtures")
class FrontendLanguageTests(unittest.TestCase):
    def test_shipped_translation_syntax(self):
        result = subprocess.run([shutil.which("node"), "--check", str(APP.with_name("i18n.js"))],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_locale_and_language_request_boundary(self):
        for scenario in ("localeSelection", "englishWorkflow", "englishXrayAfterToggle", "guardedXrayBilingualFallback", "researchXrayBilingualLayersAndContributions"):
            with self.subTest(scenario=scenario):
                result = subprocess.run([shutil.which("node"), "-e", LOCALE_FIXTURE, str(APP), scenario],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
