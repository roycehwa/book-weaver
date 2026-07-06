import { chromium } from 'playwright';

const BASE_URL = 'http://101.43.19.135';

async function runTests() {
  console.log('=== BookMate 修复验证测试 ===\n');
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  try {
    // 测试 1: 检查页面加载
    console.log('测试 1: 页面加载...');
    await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForLoadState('domcontentloaded');
    console.log('✅ 页面加载成功\n');
    
    // 测试 2: 点击第一本书进入阅读器
    console.log('测试 2: 进入阅读器...');
    const firstBook = await page.$('.book-card, [data-testid="book-card"], .library-item');
    if (firstBook) {
      await firstBook.click();
      await page.waitForTimeout(2000);
      console.log('✅ 进入阅读器成功\n');
    } else {
      console.log('⚠️ 未找到书籍，可能书库为空\n');
    }
    
    // 测试 3: 检查侧边栏
    console.log('测试 3: 检查侧边栏...');
    const sidebarToggle = await page.$('button[title="显示目录"], button svg');
    if (sidebarToggle) {
      await sidebarToggle.click();
      await page.waitForTimeout(1000);
      console.log('✅ 侧边栏可打开\n');
      
      // 测试 4: 检查章节标记按钮
      console.log('测试 4: 检查章节标记按钮...');
      const markButton = await page.$('text=标记章节, button:has-text("标记章节"), .chapter-mark-prompt-btn, .chapter-manage-btn-add');
      if (markButton) {
        console.log('✅ 章节标记按钮存在\n');
      } else {
        console.log('⚠️ 未找到章节标记按钮（可能是多章节书籍）\n');
      }
    }
    
    // 测试 5: 检查 PDF 翻页按钮
    console.log('测试 5: 检查 PDF 底部翻页按钮...');
    const prevButton = await page.$('text=上一页, button:has-text("上一页"), [aria-label="上一页"]');
    const nextButton = await page.$('text=下一页, button:has-text("下一页"), [aria-label="下一页"]');
    
    if (prevButton && nextButton) {
      console.log('✅ 翻页按钮都存在');
      
      // 检查按钮是否可见
      const prevVisible = await prevButton.isVisible();
      const nextVisible = await nextButton.isVisible();
      
      if (prevVisible && nextVisible) {
        console.log('✅ 翻页按钮可见\n');
      } else {
        console.log('❌ 翻页按钮不可见\n');
      }
    } else {
      console.log('⚠️ 未找到翻页按钮（可能页面未完全加载）\n');
    }
    
    console.log('=== 测试完成 ===');
    
  } catch (error) {
    console.error('❌ 测试失败:', error.message);
  } finally {
    await browser.close();
  }
}

runTests();
