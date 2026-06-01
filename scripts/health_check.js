#!/usr/bin/env node
/**
 * Post-deploy health check for market_sentiment.html on GitHub Pages.
 * Fetches the live page, checks key DOM values are present and non-zero.
 *
 * Run: node scripts/health_check.js
 * Exit 0 = healthy, Exit 1 = anomaly detected (alerts sent via stdout)
 */

const https = require('https');
const url = 'https://jeremygarden.github.io/ashare-etf-watcher-dash/market_sentiment.html';

function httpGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { 'User-Agent': 'health-check/1.0' } }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    }).on('error', reject);
  });
}

async function check() {
  console.log(`[health-check] ${new Date().toISOString()}`);
  console.log(`[health-check] Checking: ${url}`);

  let res;
  try {
    res = await httpGet(url);
  } catch (e) {
    console.error(`❌ FETCH FAILED: ${e.message}`);
    process.exit(1);
  }

  if (res.status !== 200) {
    console.error(`❌ HTTP ${res.status} — page not reachable`);
    process.exit(1);
  }
  console.log(`  ✅ HTTP 200 OK`);

  const html = res.body;
  const issues = [];

  // 1. Key element IDs present
  const requiredIds = [
    'compositeVal', 'threeLineVal', 'marketCoefVal',
    'sentimentVal', 'weakMoneyVal', 'gaugePointer', 'emotionBadge'
  ];
  for (const id of requiredIds) {
    if (!html.includes(`id="${id}"`)) {
      issues.push(`Missing element id="${id}"`);
    }
  }

  // 2. Static default composite value in HTML (should be a number, not 0)
  const compositeMatch = html.match(/id="compositeVal"[^>]*>(\d+)</);
  if (compositeMatch) {
    const staticVal = parseInt(compositeMatch[1]);
    if (staticVal === 0) {
      issues.push(`compositeVal static default is 0 — likely broken fallback`);
    } else {
      console.log(`  ✅ compositeVal static default: ${staticVal}`);
    }
  }

  // 3. calcSentiment signature check — scan for the known bad param pattern
  if (html.includes('calcSentiment(ztCount, avgTurnover')) {
    issues.push(`REGRESSION: calcSentiment still using wrong params (avgTurnover instead of zbRate)`);
  } else {
    console.log(`  ✅ calcSentiment params look correct`);
  }

  // 4. calcWeakMoney 2-arg pattern check
  const weakMoneyMatch = html.match(/calcWeakMoney\(zbRate,\s*sh3\)/);
  if (weakMoneyMatch) {
    issues.push(`REGRESSION: calcWeakMoney missing 3rd param (upRatio, mainNetFlow)`);
  } else {
    console.log(`  ✅ calcWeakMoney params look correct`);
  }

  // 5. Check sentiment_history.json is accessible
  const jsonUrl = 'https://jeremygarden.github.io/ashare-etf-watcher-dash/sentiment_history.json';
  try {
    const jsonRes = await httpGet(jsonUrl);
    if (jsonRes.status === 200) {
      const parsed = JSON.parse(jsonRes.body);
      const len = parsed?.history?.length || 0;
      if (len === 0) {
        issues.push(`sentiment_history.json loaded but has 0 entries`);
      } else {
        console.log(`  ✅ sentiment_history.json: ${len} entries`);
        // Check latest entry has valid composite
        const last = parsed.history[parsed.history.length - 1];
        if (!Number.isFinite(last.composite) || last.composite === 0) {
          issues.push(`Latest history entry has invalid composite: ${last.composite}`);
        } else {
          console.log(`  ✅ Latest entry date=${last.date} composite=${last.composite}`);
        }
      }
    } else {
      issues.push(`sentiment_history.json returned HTTP ${jsonRes.status}`);
    }
  } catch (e) {
    issues.push(`sentiment_history.json fetch failed: ${e.message}`);
  }

  // ===== Result =====
  console.log('');
  if (issues.length === 0) {
    console.log('✅ Health check PASSED — dashboard looks good.');
    process.exit(0);
  } else {
    console.error('❌ Health check FAILED:');
    issues.forEach(i => console.error(`   • ${i}`));
    process.exit(1);
  }
}

check();
