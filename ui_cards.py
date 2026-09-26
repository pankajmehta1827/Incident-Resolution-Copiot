"""HTML building blocks for the cockpit: queue cards and live SLA timers.

Streamlit buttons can't lay out a card (ID, severity and system on one row, a bar with the
timer beside it), so cards are HTML with an invisible button over them for the click. Timers
tick in the browser from a small script; the server only sends the seconds remaining.
All incident text is escaped before it goes into HTML.
"""
from __future__ import annotations

import time
from html import escape

CSS = """
<style>
.qc {background:#111A2C; border:1px solid #1E2A44; border-radius:10px; padding:10px 12px;
     display:flex; flex-direction:column; gap:6px; color:#E6ECF5;}
.qc.sel {background:#18264A; border-color:#5B9BFF;}
.qc-top {display:flex; align-items:center; gap:8px;}
.qc-id {font-family:'JetBrains Mono',monospace; font-size:12px; color:#C9D4E5;}
.qc-sev {font-size:11px; font-weight:700; padding:1px 6px; border-radius:4px; color:#0B1220;}
.qc-sys {margin-left:auto; font-size:12px; color:#8FA0BA;}
.qc-title {font-size:14px; line-height:1.35; display:-webkit-box; -webkit-line-clamp:2;
     -webkit-box-orient:vertical; overflow:hidden;}
.qc-rep {font-size:12px; color:#8DB4FF;}
.sla {display:flex; align-items:center; gap:8px;}
.sla-track {flex:1; height:4px; border-radius:2px; background:#24324F; overflow:hidden;}
.sla-fill {height:4px; border-radius:2px;}
.sla-t {font-family:'JetBrains Mono',monospace; font-size:12px; white-space:nowrap;}
.sla.breached .sla-fill, .sla-box.breached .sla-fill {background:#FF8A4C;}
.sla.breached .sla-t, .sla-box.breached .sla-t {color:#FF8A4C;}
.sla.risk .sla-fill, .sla-box.risk .sla-fill {background:#FF8A4C;}
.sla.risk .sla-t, .sla-box.risk .sla-t {color:#FF8A4C;}
.sla.warn .sla-fill, .sla-box.warn .sla-fill {background:#FBBF24;}
.sla.warn .sla-t, .sla-box.warn .sla-t {color:#FBBF24;}
.sla.ok .sla-fill, .sla-box.ok .sla-fill {background:#9FB0C8;}
.sla.ok .sla-t, .sla-box.ok .sla-t {color:#9FB0C8;}
.sla.met .sla-fill, .sla-box.met .sla-fill {background:#4ADE80;}
.sla.met .sla-t, .sla-box.met .sla-t {color:#4ADE80;}
.sla-box {width:196px; box-sizing:border-box; padding:10px 12px; border-radius:10px; background:#111A2C;
     border:1px solid #9FB0C8; display:flex; flex-direction:column; gap:6px;}
.sla-box.breached, .sla-box.risk {background:rgba(255,138,76,0.08); border-color:#FF8A4C;}
.sla-box.warn {border-color:#FBBF24;} .sla-box.met {border-color:#4ADE80;}
.sla-box .row {display:flex; justify-content:space-between; font-size:12px; color:#C9D4E5;}
.sla-box .sla-t {font-size:22px; font-weight:600;}
/* the whole card is the click target */
[class*="st-key-card_"] {position:relative;}
[class*="st-key-card_"] > [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
     position:absolute; inset:0; width:auto !important; z-index:2; margin:0;}
[class*="st-key-card_"] [data-testid="stButton"] {position:static; width:100%; height:100%;}
[class*="st-key-card_"] [data-testid="stButton"] button {width:100%; height:100%; opacity:0; cursor:pointer;}
[class*="st-key-card_"] [data-testid="stButton"] button:focus-visible {opacity:1; background:transparent;
     outline:2px solid #5B9BFF;}
</style>
"""

# Ticks every [data-sla-deadline] element once a second. The server sends an absolute deadline
# (epoch ms), so every timer for an incident agrees however late it was drawn. Guarded so reruns
# don't stack intervals.
SCRIPT = """
<script>
(function () {
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmt(s) {
    s = Math.floor(Math.abs(s));
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (d ? d + 'd ' : '') + (d ? pad(h) : h) + ':' + pad(m) + ':' + pad(x);
  }
  function tick() {
    var now = Date.now();
    document.querySelectorAll('[data-sla-deadline]').forEach(function (el) {
      var total = parseFloat(el.dataset.slaTotal), rem = (parseFloat(el.dataset.slaDeadline) - now) / 1000;
      var cls = rem < 0 ? 'breached' : (rem < 3600 || rem < total * 0.25) ? 'risk' : (rem < 3 * 3600) ? 'warn' : 'ok';
      el.classList.remove('breached', 'risk', 'warn', 'ok'); el.classList.add(cls);
      var used = total > 0 ? Math.max(0, Math.min(100, 100 * (1 - rem / total))) : 0;
      var t = el.querySelector('.sla-t'), f = el.querySelector('.sla-fill'), c = el.querySelector('.sla-cap'), u = el.querySelector('.sla-used');
      if (t) t.textContent = el.dataset.compact === '1' ? (rem < 0 ? '+' + fmt(rem) + ' over' : fmt(rem) + ' left') : fmt(rem);
      if (f) f.style.width = used.toFixed(0) + '%';
      if (c) c.textContent = rem < 0 ? 'Breached for' : 'Time to breach';
      if (u) u.textContent = used.toFixed(0) + '% used';
    });
  }
  if (!window.__slaTimer) { window.__slaTimer = setInterval(tick, 1000); }
  tick();
})();
</script>
"""

SEV_BG = {"P1": "#FF8A4C", "P2": "#FBBF24", "P3": "#9FB0C8", "P4": "#9FB0C8"}


def _deadline(rem: float, deadline_ms: float | None) -> str:
    return f"{deadline_ms if deadline_ms is not None else (time.time() + rem) * 1000:.0f}"


def _fmt(seconds: float) -> str:
    s = int(abs(seconds))
    d, h, m, x = s // 86400, (s % 86400) // 3600, (s % 3600) // 60, s % 60
    return (f"{d}d {h:02d}" if d else f"{h}") + f":{m:02d}:{x:02d}"


def _cls(rem: float, total: float) -> str:
    if rem < 0:
        return "breached"
    if rem < 3600 or rem < total * 0.25:
        return "risk"
    return "warn" if rem < 3 * 3600 else "ok"


def _used(rem: float, total: float) -> int:
    return int(max(0, min(100, 100 * (1 - rem / total)))) if total else 0


def sla_bar(rem: float | None, total: float, met: bool = False, deadline_ms: float | None = None) -> str:
    """Compact bar + timer for a queue card."""
    if rem is None:
        return '<div class="sla ok"><div class="sla-track"></div><span class="sla-t">No SLA</span></div>'
    if met:
        return ('<div class="sla met"><div class="sla-track"><div class="sla-fill" style="width:100%"></div></div>'
                '<span class="sla-t">Resolved</span></div>')
    text = f"+{_fmt(rem)} over" if rem < 0 else f"{_fmt(rem)} left"
    return (f'<div class="sla {_cls(rem, total)}" data-sla-deadline="{_deadline(rem, deadline_ms)}" data-sla-total="{total:.0f}" data-compact="1">'
            f'<div class="sla-track"><div class="sla-fill" style="width:{_used(rem, total)}%"></div></div>'
            f'<span class="sla-t">{text}</span></div>')


def sla_box(rem: float | None, total: float, met: bool = False, deadline_ms: float | None = None) -> str:
    """Large SLA timer for the incident header."""
    if rem is None:
        return ""
    if met:
        return ('<div class="sla-box met"><div class="row"><span>SLA</span><span></span></div>'
                '<div class="sla-t">Met</div><div class="sla-track"><div class="sla-fill" style="width:100%">'
                '</div></div></div>')
    return (f'<div class="sla-box {_cls(rem, total)}" data-sla-deadline="{_deadline(rem, deadline_ms)}" data-sla-total="{total:.0f}">'
            f'<div class="row"><span class="sla-cap">{"Breached for" if rem < 0 else "Time to breach"}</span>'
            f'<span class="sla-used">{_used(rem, total)}% used</span></div>'
            f'<div class="sla-t">{_fmt(rem)}</div>'
            f'<div class="sla-track"><div class="sla-fill" style="width:{_used(rem, total)}%"></div></div></div>')


def queue_card(number: str, sev: str, system: str, title: str, sla_html: str, repeat: int = 0,
               selected: bool = False, ask: bool = False) -> str:
    if ask:
        top = '<div class="qc-top"><span class="qc-id" style="color:#8DB4FF">Agent search</span></div>'
    else:
        top = (f'<div class="qc-top"><span class="qc-id">{escape(number)}</span>'
               f'<span class="qc-sev" style="background:{SEV_BG.get(sev, "#9FB0C8")}">{escape(sev)}</span>'
               f'<span class="qc-sys">{escape(system)}</span></div>')
    rep = f'<div class="qc-rep">Repeat · {repeat} linked incidents</div>' if repeat else ""
    return (f'<div class="qc{" sel" if selected else ""}">{top}<div class="qc-title">{escape(title)}</div>'
            f'{sla_html}{rep}</div>')
