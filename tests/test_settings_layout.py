"""The Settings menu, the pages on the tabs and the folding sections stay complete as settings are added."""
import re
from pathlib import Path

from custom_components.sentient.websocket import DEPENDS, GROUPS, SECTIONS

PANEL = Path(__file__).parents[1] / "custom_components/sentient/frontend/sentient-panel.js"


def _menus_and_tabs():
    js = PANEL.read_text()
    block = js[js.index("const menus = ["):js.index("const placed = new Set")]
    menus = [re.findall(r'"([a-z_]+)"', ids) for ids in re.findall(r'"[^"]+", \[([^\]]*)\]\]', block)]
    tabs_block = js[js.index("const ON_TABS = {"):js.index("const TAB_OF")]
    on_tabs = re.findall(r'"([a-z_]+)"', "".join(re.findall(r"\[([^\]]*)\]", tabs_block)))
    return [i for m in menus for i in m], on_tabs


def test_every_settings_page_has_exactly_one_home():
    in_menus, on_tabs = _menus_and_tabs()
    for gid, *_ in GROUPS:
        homes = in_menus.count(gid) + on_tabs.count(gid)
        assert homes == 1, f"{gid} appears {homes} times across the Settings menu and the tabs"


def test_folding_sections_hold_every_setting_of_their_page_once():
    for gid, sections in SECTIONS.items():
        keys = list(next(g[4] for g in GROUPS if g[0] == gid))
        named = [n for s in sections for n in s["names"]]
        assert sorted(named) == sorted(keys), gid
        assert any(s["open"] for s in sections), f"{gid} opens with every section folded"


def test_a_hidden_setting_and_the_switch_it_waits_for_share_a_page():
    page_of = {k: g[0] for g in GROUPS for k in g[4]}
    for child, parent in DEPENDS.items():
        if child in page_of:
            assert page_of.get(parent) == page_of[child], f"{child} waits for {parent}, which is on another page"


def test_pages_moved_to_the_tabs_never_wait_for_the_settings_tab():
    # a list page opened on House, Mind or Security draws through _editorOpen(); a check for the Settings tab
    # left in one of them leaves its sheet stuck on LOADING (motion lights in 1.4.2)
    js = PANEL.read_text()
    checks = [line.strip() for line in js.splitlines() if re.search(r'_view\s*[!=]==?\s*"settings"', line)]
    allowed = ("_editorOpen() {", 'if (this._view === "settings") {')
    assert all(any(a in c for a in allowed) for c in checks), checks
