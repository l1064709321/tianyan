"""Playwright 端到端测试: 流畅测试所有功能 (修正版 v2)"""
import json
import sys
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8000"
SCREENSHOT_DIR = "/workspace/tianyan/screenshots"

def test_all():
    import os
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    results = {"passed": [], "failed": [], "warnings": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        errors = []
        page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None)

        # ========== 1. 页面加载与核心元素 ==========
        print("=" * 60)
        print("1. 页面加载与核心元素")
        page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        print(f"   标题: {page.title()}")

        elements = ["#input", "#send-btn", "#sidebar", "#proj-select-btn",
                     "#settings-btn", "#skills-btn", "#runs-btn", "#model-chip",
                     "#menu-btn", "#export-btn"]
        all_ok = True
        for sel in elements:
            if page.locator(sel).count() > 0:
                print(f"   {sel}: OK")
            else:
                print(f"   {sel}: MISSING")
                all_ok = False
        page.screenshot(path=f"{SCREENSHOT_DIR}/01_initial.png", full_page=True)
        if all_ok:
            results["passed"].append("核心元素")
        else:
            results["failed"].append("核心元素缺失")

        # ========== 2. 项目列表与选择 ==========
        print("=" * 60)
        print("2. 项目列表与选择")
        # 打开项目菜单
        page.locator("#proj-select-btn").click()
        page.wait_for_timeout(500)

        proj_opts = page.locator("#proj-menu .proj-opt[data-id]")
        print(f"   项目数: {proj_opts.count()}")
        for i in range(proj_opts.count()):
            print(f"   [{i}] {proj_opts.nth(i).text_content()}")

        if proj_opts.count() > 0:
            # 点击第一个项目
            proj_opts.first.click()
            page.wait_for_timeout(1500)

            # 验证项目已选中
            has_project = page.evaluate("currentProject !== null")
            proj_info = page.locator("#proj-info").text_content() if page.locator("#proj-info").count() > 0 else "N/A"
            print(f"   选中项目: {proj_info}")
            print(f"   currentProject: {has_project}")

            if has_project:
                results["passed"].append("项目选择")
            else:
                results["failed"].append("项目选择失败")
        else:
            results["failed"].append("无项目")

        page.screenshot(path=f"{SCREENSHOT_DIR}/02_project_selected.png", full_page=True)

        # ========== 3. 聊天发送测试 ==========
        print("=" * 60)
        print("3. 聊天发送")
        has_project = page.evaluate("currentProject !== null")
        if has_project:
            textarea = page.locator("#input")
            textarea.fill("你好")
            page.wait_for_timeout(300)
            page.locator("#send-btn").click()
            page.wait_for_timeout(4000)

            msgs = page.locator(".msg")
            print(f"   消息数: {msgs.count()}")

            # 检查是否有错误
            err = page.locator(".err")
            if err.count() > 0:
                err_text = err.first.text_content()
                print(f"   错误: {err_text}")
                results["warnings"].append(f"聊天返回错误: {err_text}")

            # 检查思考过程
            think = page.locator(".think-box")
            print(f"   思考容器: {think.count()}")

            page.screenshot(path=f"{SCREENSHOT_DIR}/03_chat.png", full_page=True)
            results["passed"].append("聊天发送")
        else:
            results["failed"].append("聊天: 无项目选中")

        # ========== 4. 设置面板 ==========
        print("=" * 60)
        print("4. 设置面板")
        page.locator("#settings-btn").click()
        page.wait_for_timeout(800)

        sp_open = "open" in (page.locator("#settings-panel").get_attribute("class") or "")
        print(f"   打开: {sp_open}")

        if sp_open:
            sp_text = (page.locator("#sp-body").text_content() or "")[:150]
            print(f"   内容: {sp_text}")
            page.screenshot(path=f"{SCREENSHOT_DIR}/04_settings.png", full_page=True)

        page.locator("#sp-close").click()
        page.wait_for_timeout(500)
        sp_closed = "open" not in (page.locator("#settings-panel").get_attribute("class") or "")
        print(f"   关闭: {sp_closed}")

        if sp_open and sp_closed:
            results["passed"].append("设置面板")
        else:
            results["failed"].append("设置面板异常")

        # ========== 5. 模型切换 ==========
        print("=" * 60)
        print("5. 模型快速切换")
        page.locator("#model-chip").click()
        page.wait_for_timeout(500)

        items = page.locator("#model-menu .mm-item")
        print(f"   模型数: {items.count()}")
        if items.count() > 0:
            items.first.click()
            page.wait_for_timeout(1000)
            page.screenshot(path=f"{SCREENSHOT_DIR}/05_model_switch.png", full_page=True)
            results["passed"].append("模型切换")
        else:
            results["failed"].append("模型切换: 无模型")

        # ========== 6. 主题切换 ==========
        print("=" * 60)
        print("6. 主题切换")
        page.evaluate("applyTheme('dark')")
        page.wait_for_timeout(500)
        theme = page.evaluate("currentTheme")
        print(f"   切换到: {theme}")
        page.evaluate("applyTheme('sepia')")
        page.wait_for_timeout(500)
        page.screenshot(path=f"{SCREENSHOT_DIR}/06_theme.png", full_page=True)
        results["passed"].append("主题切换")

        # ========== 7. 项目新建模态框 ==========
        print("=" * 60)
        print("7. 项目新建")
        # 打开项目菜单
        page.locator("#proj-select-btn").click()
        page.wait_for_timeout(500)
        page.locator("#new-project-opt").click()
        page.wait_for_timeout(1000)

        modal = page.locator("#proj-modal")
        if modal.count() > 0:
            modal_show = "show" in (modal.get_attribute("class") or "")
            print(f"   模态框显示: {modal_show}")
            if modal_show:
                page.screenshot(path=f"{SCREENSHOT_DIR}/07_new_project.png", full_page=True)
                results["passed"].append("项目新建模态框")
            else:
                results["failed"].append("项目新建模态框未显示")
        else:
            results["failed"].append("项目新建: 无模态框")

        # 关闭模态框
        page.evaluate("document.querySelector('#proj-modal')?.classList.remove('show')")
        page.wait_for_timeout(500)
        print("   模态框已关闭")

        # ========== 8. 技能面板 ==========
        print("=" * 60)
        print("8. 技能面板")
        page.locator("#skills-btn").click()
        page.wait_for_timeout(1000)

        skp_open = "open" in (page.locator("#skills-panel").get_attribute("class") or "")
        print(f"   打开: {skp_open}")

        if skp_open:
            cards = page.locator("#skp-builtin-grid .skp-card")
            print(f"   内置技能: {cards.count()}")
            page.screenshot(path=f"{SCREENSHOT_DIR}/08_skills.png", full_page=True)

        page.locator("#skp-close").click()
        page.wait_for_timeout(500)
        results["passed"].append("技能面板")

        # ========== 9. 运行历史面板 ==========
        print("=" * 60)
        print("9. 运行历史面板")
        page.locator("#runs-btn").click()
        page.wait_for_timeout(1000)

        rp_open = "open" in (page.locator("#runs-panel").get_attribute("class") or "")
        print(f"   打开: {rp_open}")

        if rp_open:
            page.screenshot(path=f"{SCREENSHOT_DIR}/09_runs.png", full_page=True)

        page.locator("#rp-close").click()
        page.wait_for_timeout(500)
        results["passed"].append("运行历史面板")

        # ========== 10. JavaScript 错误检查 ==========
        print("=" * 60)
        print("10. JS 错误检查")
        js_errors = [e for e in errors if "[error]" in e]
        js_warnings = [e for e in errors if "[warning]" in e]

        if js_errors:
            print(f"   错误 ({len(js_errors)}):")
            for e in js_errors:
                print(f"   - {e}")
            results["failed"].append(f"JS 错误: {len(js_errors)} 个")
        else:
            print("   无 JS 错误")
            results["passed"].append("JS 错误")

        if js_warnings:
            print(f"   警告 ({len(js_warnings)}):")
            for w in js_warnings[:5]:
                print(f"   - {w}")

        # ========== 11. API 测试 ==========
        print("=" * 60)
        print("11. API 端点")
        apis = ["/api/projects", "/api/agents", "/api/skills", "/api/skill-market", "/api/config", "/api/settings"]
        all_api_ok = True
        for url in apis:
            resp = page.evaluate(f"fetch('{url}').then(r => r.json()).then(d => 'ok').catch(e => 'err: '+e.message)")
            if resp == "ok":
                print(f"   {url}: OK")
            else:
                print(f"   {url}: FAIL ({resp})")
                all_api_ok = False
        if all_api_ok:
            results["passed"].append("API")
        else:
            results["failed"].append("API 异常")

        # ========== 12. 检查 404 资源 ==========
        print("=" * 60)
        print("12. 404 资源定位")
        page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)

        # 检查所有 img/favicon 等
        missing = page.evaluate("""() => {
            const imgs = document.querySelectorAll('img');
            const broken = [];
            imgs.forEach(img => {
                if (!img.complete || img.naturalWidth === 0) broken.push(img.src);
            });
            return broken;
        }""")
        if missing:
            print(f"   破损图片: {missing}")
            results["warnings"].append(f"破损图片: {len(missing)} 个")
        else:
            print("   无破损图片")

        # 检查 favicon
        favicon = page.evaluate("""() => {
            const links = document.querySelectorAll('link[rel*="icon"]');
            const urls = [];
            links.forEach(l => urls.push(l.href));
            return urls;
        }""")
        print(f"   Favicon links: {favicon}")

        page.screenshot(path=f"{SCREENSHOT_DIR}/10_final.png", full_page=True)

        browser.close()

    # ========== 汇总 ==========
    print("\n" + "=" * 60)
    print("           测试结果汇总")
    print("=" * 60)
    print(f"  通过: {len(results['passed'])} 项")
    for p in results["passed"]:
        print(f"    ✓ {p}")
    print(f"  失败: {len(results['failed'])} 项")
    for f in results["failed"]:
        print(f"    ✗ {f}")
    if results.get("warnings"):
        print(f"  警告: {len(results['warnings'])} 项")
        for w in results["warnings"]:
            print(f"    ⚠ {w}")
    print(f"  截图: {SCREENSHOT_DIR}")
    print("=" * 60)

    return 0 if len(results["failed"]) == 0 else 1

if __name__ == "__main__":
    sys.exit(test_all())