import { chromium } from 'playwright';

const BASE_URL = 'http://101.43.19.135';
const SINGLE_CHAPTER_BOOK_ID = 'ddc61343-17a6-409f-b205-d445076182cb';

async function runTests() {
  console.log('=== BookMate 单章书籍修复验证 ===\n');
  
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  try {
    // 访问单章书籍的阅读器
    console.log('测试: 访问单章书籍阅读器...');
    await page.goto(`${BASE_URL}/reader/${SINGLE_CHAPTER_BOOK_ID}`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);
    
    // 截图保存
    await page.screenshot({ path: '/tmp/single-chapter-test.png', fullPage: true });
    console.log('✅ 阅读器页面已加载，截图保存\n');
    
    // 检查页面文本内容
    const pageText = await page.evaluate(() => document.body.innerText);
    
    // 检查章节标记按钮
    console.log('检查章节标记按钮...');
    const hasMarkChapter = pageText.includes('标记章节');
    console.log(hasMarkChapter ? '✅ 章节标记按钮存在' : '❌ 章节标记按钮未找到');
    
    // 检查翻页按钮
    console.log('\n检查翻页按钮...');
    const hasPrevButton = pageText.includes('上一页');
    const hasNextButton = pageText.includes('下一页');
    console.log(hasPrevButton ? '✅ 上一页按钮存在' : '❌ 上一页按钮未找到');
    console.log(hasNextButton ? '✅ 下一页按钮存在' : '❌ 下一页按钮未找到');
    
    // 统计结果
    console.log('\n=== 测试结果汇总 ===');
    const passed = [hasMarkChapter, hasPrevButton, hasNextButton].filter(Boolean).length;
    console.log(`通过: ${passed}/3`);
    
    if (passed === 3) {
      console.log('✅ 所有修复验证通过！');
    } else {
      console.log('⚠️ 部分测试未通过，需要进一步检查');
    }
    
  } catch (error) {
    console.error('❌ 测试失败:', error.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

runTests();
