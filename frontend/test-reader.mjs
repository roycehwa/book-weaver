import { chromium } from 'playwright';

const BASE_URL = 'http://101.43.19.135';

async function runTests() {
  console.log('=== BookMate 阅读器修复验证 ===\n');
  
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  try {
    // 直接访问第一本书的阅读器
    console.log('测试: 访问阅读器...');
    await page.goto(`${BASE_URL}/reader/1`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);
    
    // 截图保存
    await page.screenshot({ path: '/tmp/reader-test.png', fullPage: true });
    console.log('✅ 阅读器页面已加载，截图保存到 /tmp/reader-test.png\n');
    
    // 检查页面内容
    const html = await page.content();
    
    // 检查章节标记相关元素
    console.log('检查章节标记功能...');
    const hasMarkChapter = html.includes('标记章节') || html.includes('chapter-mark') || html.includes('chapter-management');
    console.log(hasMarkChapter ? '✅ 章节标记相关元素存在' : '⚠️ 章节标记元素未找到');
    
    // 检查翻页按钮
    console.log('\n检查翻页按钮...');
    const hasPrevButton = html.includes('上一页') || html.includes('prev-page');
    const hasNextButton = html.includes('下一页') || html.includes('next-page');
    console.log(hasPrevButton ? '✅ 上一页按钮存在' : '⚠️ 上一页按钮未找到');
    console.log(hasNextButton ? '✅ 下一页按钮存在' : '⚠️ 下一页按钮未找到');
    
    // 检查溢出样式
    console.log('\n检查布局样式...');
    const hasOverflowAuto = html.includes('overflow-auto');
    console.log(hasOverflowAuto ? '✅ overflow-auto 样式已应用' : '⚠️ overflow-auto 样式未找到');
    
    console.log('\n=== 测试完成 ===');
    
  } catch (error) {
    console.error('❌ 测试失败:', error.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

runTests();
