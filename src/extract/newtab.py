"""进入语义探针: 链接型导航(≈ 浏览器右键 → "在新标签页中打开").

2026-09-08 实证(矩阵补全): 三类"直达"导航(脚本 goto / 地址栏 typed 裸 URL)对商品详情页一律
返回空壳(无 .desc-root/价格/推荐/评论问答), 而真实渠道点击(足迹/收藏, URL 带渠道 mi_id+spm)
与"地址栏手输带有效 mi_id 的 URL"都完整渲染。判定: 触发渲染的是 URL 里的**有效 mi_id 参数**,
不是导航类别。本模块补测最后一类真实导航 — **链接型打开**(用户在某个页面看到链接后
"新标签页打开"): 从允许的中立页注入临时 <a href=item.htm?id=X>, Playwright 可信
Ctrl+Click(前台新标签, 与右键→新标签同一链接导航管道; 退中键), 对落地弹窗跑与
probe_entry 完全相同的 ENTRY_PROBE_JS 信号 → 与裸 goto 行可直接对比。

只读、零写; 单标签卫生(弹窗 finally 关闭); captcha 人工交接; URL 白名单(仅淘宝/天猫).
已知限制: 浏览器右键菜单(原生)无法脚本化; 合成 Ctrl+Click/中键可能被来源页 JS
preventDefault(实测淘宝首页拦截) → 自动退 "新标签 + referer 导航" 并记录 opened_via。
"""


def _taobao_link_href(pid: str) -> str:
    return f"https://item.taobao.com/item.htm?id={pid}"


async def open_link_new_tab(page, href: str, timeout_ms: int = 25000):
    """Playwright 可信点击在来源页上打开 href 链接 → 返回新标签 popup(或 None).

    模拟"右键 → 在新标签页中打开": Ctrl+Click(前台新标签, 最接近)优先,
    退 button='middle'(Chromium 中键=后台新标签, 同一链接导航管道)。调用方负责:
    先把链接真实挂到 page DOM(见 inject_probe_anchor), 用毕关闭 popup。
    """
    anchor = page.locator("#dsh_probe_link")
    last_err: str | None = None
    for method in ("ctrl_click", "middle_click"):
        try:
            async with page.expect_popup(timeout=timeout_ms) as pi:
                if method == "ctrl_click":
                    await anchor.click(modifiers=["Control"])
                else:
                    await anchor.click(button="middle")
            popup = pi.value
            return popup, method, None
        except Exception as exc:  # noqa: BLE001 — 每种方法都可能超时/被页 JS 拦截
            last_err = str(exc)
            continue
    return None, "", (last_err or "link 激活失败(两种方法均未弹出新标签)")


async def inject_probe_anchor(page, href: str) -> str:
    """把探针链接临时挂到当前页(页面本身不可见, 用完即删). 返回实际 href."""
    return await page.evaluate(
        """(href) => {
          const old = document.getElementById('dsh_probe_link');
          if (old) old.remove();
          const a = document.createElement('a');
          a.id = 'dsh_probe_link';
          a.href = href;
          a.textContent = 'probe';
          a.style.cssText = 'position:fixed;left:0;top:0;width:2px;height:2px;'
            + 'opacity:0.01;z-index:999999;';
          document.body.appendChild(a);
          return a.href;
        }""",
        href,
    )


async def remove_probe_anchor(page) -> None:
    try:
        await page.evaluate(
            """() => { const a = document.getElementById('dsh_probe_link');
                       if (a) a.remove(); }"""
        )
    except Exception:  # noqa: BLE001 — 清理尽力而为
        pass


async def probe_popup_entry(popup, *, href: str, referer: str, entry_label: str,
                            max_wait_ms: int = 30000) -> dict:
    """对链接型打开落地的新标签页跑 probe_entry 同构信号(详情/推荐/评论/问答/价格).

    与 probe_entry(desc.py) 输出键一致, 保证矩阵行可直接对比; 弹窗由调用方 finally 关闭。
    """
    from src.browser.pacing import human_delay
    from src.browser.scroll import scroll_to_bottom

    from src.extract.selectors import ENTRY_PROBE_JS

    err: str | None = None
    try:
        await popup.wait_for_load_state("domcontentloaded", timeout=max_wait_ms)
    except Exception as exc:  # noqa: BLE001
        err = f"弹窗 domcontentloaded 超时/失败: {str(exc)[:120]}"
    if not err:
        # 滚动到底触发推荐区懒加载(与 probe_entry 同口径), 再回顶部。
        try:
            await scroll_to_bottom(popup)
            await human_delay(1.0, 1.8)
        except Exception:  # noqa: BLE001
            pass
        try:
            probe = await popup.evaluate(ENTRY_PROBE_JS)
        except Exception as exc:  # noqa: BLE001
            probe, err = {}, f"ENTRY_PROBE_JS 失败: {str(exc)[:120]}"
    else:
        probe = {}
    probe["entry"] = entry_label
    probe["goto_url"] = href
    probe["referer"] = referer
    probe["landed_url"] = (popup.url or "")[:240]
    probe["landed_has_miid"] = bool(
        popup.url and ("mi_id=" in popup.url or "miid=" in popup.url)
    )
    if err:
        probe["probe_error"] = err
    return probe


async def probe_open_newtab(product_url_or_id: str, source: str = "bare") -> dict:
    """从指定中立来源页"链接型打开"商品 → 返回 ENTRY_PROBE 信号.

    只读、零写。source 目前仅 bare(淘宝首页, referer=首页, 上下文最中性 —
    类"手头有链接直接右键新标签打开")。落地弹窗跑 ENTRY_PROBE_JS 后立即关闭。
    """
    from src.browser.pacing import human_delay
    from src.browser.session import get_session

    from src.extract.product import _to_product_id

    pid = _to_product_id(product_url_or_id)
    src = str(source or "bare").strip().lower()
    allowed = {"bare": "https://www.taobao.com/"}
    if src not in allowed:
        return {"error": f"未知来源 '{source}'; 支持: bare(淘宝首页注入, referer=首页)"}
    source_url = allowed[src]
    href = _taobao_link_href(pid)

    session = get_session()
    page = await session.start()
    await page.goto(source_url, wait_until="domcontentloaded")
    await session.guard_captcha(page)
    await human_delay(1.2, 2.2)
    await inject_probe_anchor(page, href)

    popup, opened_via, err = await open_link_new_tab(page, href)
    await remove_probe_anchor(page)
    fallback_err: str | None = None
    if popup is None:
        # 合成 Ctrl+Click/中键被来源页 JS 拦截(如淘宝首页 SPA 吞掉 auxclick) —
        # 真实右键菜单是浏览器原生、无法脚本化。退回"新标签 + referer 导航"(仍与裸
        # goto 不同: 新标签上下文 + referer=来源页), 并如实记录 opened_via 与拦截原因。
        fallback_err = err
        try:
            popup = await page.context.new_page()
            await popup.goto(href, referer=source_url, wait_until="domcontentloaded")
            opened_via = "fallback_new_page_referer"
        except Exception as exc:  # noqa: BLE001
            return {
                "error": f"链接型打开失败: 合成点击被拦截({err}); fallback 新标签导航也失败: {str(exc)[:120]}",
                "entry": "newtab",
                "goto_url": href,
                "referer": source_url,
                "source_url": source_url,
                "opened_via": "none",
            }

    out: dict = {}
    try:
        if session.guard_captcha is not None:
            try:
                await session.guard_captcha(popup)
            except Exception:  # noqa: BLE001 — 弹窗 captcha 由 guard 处理/上浮
                pass
        out = await probe_popup_entry(popup, href=href, referer=source_url, entry_label="newtab")
        out["source_url"] = source_url
        out["opened_via"] = opened_via or "unknown"
        if fallback_err:
            out["ctrl_middle_blocked"] = f"合成 Ctrl+Click/中键被来源页 JS 拦截: {fallback_err}"
    finally:
        if not popup.is_closed():
            try:
                await popup.close()
            except Exception:  # noqa: BLE001
                pass
        out["popup_closed"] = True
    return out
