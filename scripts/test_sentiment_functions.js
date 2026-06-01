#!/usr/bin/env node
/**
 * Unit tests for market_sentiment.html JS calculation functions.
 * Extract and verify all scoring functions before any deploy.
 *
 * Run: node scripts/test_sentiment_functions.js
 */

// ===== Replicate functions from market_sentiment.html =====

function normalize(val, min, max) {
  if (max === min) return 50;
  const clamped = Math.max(min, Math.min(max, val));
  return Math.round((clamped - min) / (max - min) * 100);
}

function calcMarketCoef(sh3, upRatio, northFlow) {
  const a = normalize(sh3, -2, 2);
  const b = normalize(upRatio * 100, 30, 70);
  const c = normalize(northFlow, -80, 80);
  return Math.round(a * 0.45 + b * 0.35 + c * 0.20);
}

function calcSentiment(ztCount, zbRate, shChg) {
  const a = normalize(ztCount, 0, 120);
  const b = normalize(100 - zbRate, 20, 90);
  const c = normalize(shChg, -2, 2);
  return Math.round(a * 0.55 + b * 0.30 + c * 0.15);
}

function calcWeakMoney(zbRate, upRatio, mainNetYi) {
  const a = normalize(zbRate, 0, 60);
  const b = normalize((1 - upRatio) * 100, 20, 80);
  const c = normalize(-mainNetYi, -3000, 3000);
  return Math.round(a * 0.50 + b * 0.30 + c * 0.20);
}

function calcThreeLine(market, sentiment, weakMoney) {
  return Math.round((market + sentiment + (100 - weakMoney)) / 3);
}

function calcComposite(market, sentiment, weakMoney, threeLine) {
  return Math.round(market * 0.35 + sentiment * 0.25 + (100 - weakMoney) * 0.20 + threeLine * 0.20);
}

function calcZBRate(zbCount, ztCount) {
  if (ztCount + zbCount === 0) return 0;
  return Math.round(zbCount / (ztCount + zbCount) * 100 * 10) / 10;
}

// ===== Test harness =====

let passed = 0, failed = 0;

function assert(label, actual, expected, tolerance = 0) {
  const ok = Math.abs(actual - expected) <= tolerance;
  if (ok) {
    console.log(`  ✅ ${label}: ${actual}`);
    passed++;
  } else {
    console.error(`  ❌ ${label}: got ${actual}, expected ${expected} (±${tolerance})`);
    failed++;
  }
}

function assertRange(label, actual, min, max) {
  const ok = actual >= min && actual <= max;
  if (ok) {
    console.log(`  ✅ ${label}: ${actual} (in [${min}, ${max}])`);
    passed++;
  } else {
    console.error(`  ❌ ${label}: ${actual} NOT in [${min}, ${max}]`);
    failed++;
  }
}

// ===== Test cases =====

console.log('\n=== normalize ===');
assert('normalize mid', normalize(0, -2, 2), 50);
assert('normalize max clamp', normalize(5, -2, 2), 100);
assert('normalize min clamp', normalize(-5, -2, 2), 0);
assert('normalize 0.12 in [-2,2]', normalize(0.12, -2, 2), 53, 1);

console.log('\n=== calcZBRate ===');
assert('zbRate 38/(54+38)', calcZBRate(38, 54), 41.3, 0.1);
assert('zbRate zero/zero', calcZBRate(0, 0), 0);

console.log('\n=== calcMarketCoef ===');
// sh3=0.12 (slight up), upRatio=0.55, northFlow=10亿 → moderate positive
const m1 = calcMarketCoef(0.12, 0.55, 10);
assertRange('market moderate positive', m1, 40, 70);
// sh3=-1.5 (down day), upRatio=0.30, northFlow=-50亿 → weak
const m2 = calcMarketCoef(-1.5, 0.30, -50);
assertRange('market weak day', m2, 0, 30);

console.log('\n=== calcSentiment ===');
// REGRESSION: ztCount=54, zbRate=41.3, shChg=0
// normalize(54,0,120)=45, normalize(100-41.3,20,90)=55, normalize(0,-2,2)=50
// → 45*0.55 + 55*0.30 + 50*0.15 = 24.75+16.5+7.5 = 48.75 → 49
const s1 = calcSentiment(54, 41.3, 0);
assertRange('sentiment normal day (54 zt, zbRate=41.3)', s1, 40, 55);
// Strong day: ztCount=120, zbRate=10, shChg=2 → high
const s2 = calcSentiment(120, 10, 2);
assertRange('sentiment hot day', s2, 70, 100);
// Weak day: ztCount=10, zbRate=70, shChg=-2 → low
const s3 = calcSentiment(10, 70, -2);
assertRange('sentiment weak day', s3, 0, 25);
// CRITICAL: must not collapse to 0 with typical real-world values
assert('sentiment not zero', s1 > 0, true);

console.log('\n=== calcWeakMoney ===');
// zbRate=41.3, upRatio=0.55, mainNetYi=-50 → moderate weak
const w1 = calcWeakMoney(41.3, 0.55, -50);
assertRange('weakMoney moderate', w1, 40, 75);
// zbRate=10, upRatio=0.65, mainNetYi=200 → low (good market)
const w2 = calcWeakMoney(10, 0.65, 200);
assertRange('weakMoney good day', w2, 0, 35);
// CRITICAL: no NaN (previously crashed because 3rd arg was missing)
assert('weakMoney not NaN', Number.isFinite(w1), true);

console.log('\n=== calcThreeLine ===');
const tl1 = calcThreeLine(29, 28, 70);
// (29 + 28 + 30) / 3 = 29
assert('threeLine formula', tl1, 29, 1);

console.log('\n=== calcComposite ===');
const comp1 = calcComposite(29, 28, 70, 29);
// 29*0.35 + 28*0.25 + 30*0.20 + 29*0.20 = 10.15+7+6+5.8 = 28.95 → 29
assert('composite 05/15 values', comp1, 29, 2);

console.log('\n=== INTEGRATION: fetchAllData param consistency ===');
// Simulate what fetchAllData now passes after the bug fix:
// calcSentiment(ztCount, zbRate, sh3)  <-- zbRate and sh3 (index chg %)
// calcWeakMoney(zbRate, upRatio, mainNetFlow)
const ztCount = 54, zbRate = 41.3, sh3 = 0.05, upRatio = 0.55, mainNetFlow = -50;
const market_i  = calcMarketCoef(sh3, upRatio, 5);
const sentiment_i = calcSentiment(ztCount, zbRate, sh3);   // FIXED params
const weak_i    = calcWeakMoney(zbRate, upRatio, mainNetFlow);  // FIXED params
const threeLine_i = calcThreeLine(market_i, sentiment_i, weak_i);
const composite_i = calcComposite(market_i, sentiment_i, weak_i, threeLine_i);

console.log(`  market=${market_i} sentiment=${sentiment_i} weak=${weak_i} threeLine=${threeLine_i} composite=${composite_i}`);
assertRange('integration composite in range', composite_i, 10, 60);
assert('integration composite not zero', composite_i > 0, true);
assert('integration all finite', [market_i,sentiment_i,weak_i,threeLine_i,composite_i].every(Number.isFinite), true);

// ===== Summary =====

console.log(`\n${'='.repeat(40)}`);
console.log(`RESULT: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.error('❌ TESTS FAILED — do not deploy!');
  process.exit(1);
} else {
  console.log('✅ All tests passed — safe to deploy.');
  process.exit(0);
}
