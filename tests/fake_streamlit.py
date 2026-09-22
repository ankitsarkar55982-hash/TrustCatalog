"""
A minimal fake of the `streamlit` and `plotly.express` APIs, used ONLY to
execute the dashboard page scripts headlessly in this offline sandbox (no
network access = no `pip install streamlit`). This is not a UI test - it
cannot catch rendering/CSS/layout problems - but it DOES execute every
line of top-level Python in each page (which is where the real logic
lives: form handling, database calls, authorization checks) and will
raise on any NameError, AttributeError, or unhandled exception exactly
like the real Streamlit runtime would.

Not shipped to the user - lives only under tests/, and is not imported by
anything in src/ or dashboard/.
"""

import sys
import types


class StopScript(BaseException):
    """
    Raised by fake st.stop()/st.rerun() - mirrors real Streamlit, where
    RerunException/StopException subclass BaseException (not Exception)
    specifically so that a page's own `except Exception` blocks (such as
    src.session.friendly_errors) never accidentally swallow a stop/rerun.
    """


class _SessionState(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __setattr__(self, key, value):
        self[key] = value


class _Widget:
    """
    Generic stand-in for anything st.columns()/st.container()/st.expander()/
    st.sidebar returns. Real Streamlit column/container objects expose the
    FULL top-level widget API (col.metric(...), col.button(...), etc.) - so
    this delegates any unknown attribute to whichever FakeStreamlit instance
    is currently installed, rather than hard-coding a duplicate method list
    that would inevitably drift out of sync with FakeStreamlit itself.
    """
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        current_st = sys.modules.get("streamlit")
        if current_st is not None and hasattr(current_st, name):
            return getattr(current_st, name)
        raise AttributeError(
            f"fake st.columns()/container() has no attribute '{name}' "
            f"(not implemented on FakeStreamlit either)"
        )


class _Form(_Widget):
    def __init__(self, submitted):
        self._submitted = submitted


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = _SessionState()
        self.sidebar = _Widget()
        self._form_submits = {}   # form_key -> bool, set by the test harness
        self._button_clicks = set()  # button keys/labels that should read True
        self._inputs = {}          # widget key -> forced return value

    # ---- layout / no-ops ----
    def set_page_config(self, **kw): pass
    def markdown(self, *a, **kw): pass
    def write(self, *a, **kw): pass
    def caption(self, *a, **kw): pass
    def title(self, *a, **kw): pass
    def subheader(self, *a, **kw): pass
    def header(self, *a, **kw): pass
    def divider(self, *a, **kw): pass
    def code(self, *a, **kw): pass
    def json(self, *a, **kw): pass
    def progress(self, *a, **kw): pass
    def image(self, *a, **kw): pass

    def success(self, msg, *a, **kw): print(f"  [st.success] {msg}")
    def error(self, msg, *a, **kw): print(f"  [st.error]   {msg}")
    def warning(self, msg, *a, **kw): print(f"  [st.warning] {msg}")
    def info(self, msg, *a, **kw): print(f"  [st.info]    {msg}")

    def stop(self):
        raise StopScript()

    def rerun(self):
        raise StopScript("rerun")

    # ---- containers ----
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Widget() for _ in range(n)]

    def container(self, *a, **kw): return _Widget()
    def expander(self, *a, **kw): return _Widget()
    def tabs(self, labels): return [_Widget() for _ in labels]
    def form(self, key, **kw):
        return _Form(self._form_submits.get(key, False))

    # ---- widgets: return a forced value if the harness set one, else a default ----
    def _val(self, key, default):
        return self._inputs.get(key, default)

    def text_input(self, label, value="", key=None, **kw):
        return self._val(key or label, value)

    def text_area(self, label, value="", key=None, **kw):
        return self._val(key or label, value)

    def number_input(self, label, min_value=0, max_value=None, value=None, key=None, **kw):
        default = value if value is not None else min_value
        return self._val(key or label, default)

    def selectbox(self, label, options, key=None, **kw):
        options = list(options)
        return self._val(key or label, options[0] if options else None)

    def multiselect(self, label, options, default=None, key=None, **kw):
        return self._val(key or label, default or [])

    def radio(self, label, options, key=None, **kw):
        options = list(options)
        return self._val(key or label, options[0] if options else None)

    def checkbox(self, label, value=False, key=None, **kw):
        return self._val(key or label, value)

    def slider(self, label, min_value=0, max_value=100, value=None, key=None, **kw):
        return self._val(key or label, value if value is not None else min_value)

    def button(self, label, key=None, **kw):
        return (key or label) in self._button_clicks

    def file_uploader(self, label, type=None, key=None, **kw):
        return self._val(key or label, None)  # no file uploaded, by default

    def form_submit_button(self, label="Submit", **kw):
        return True  # the enclosing _Form already gates whether we act on it

    def download_button(self, *a, **kw): return False

    def page_link(self, *a, **kw): pass

    def dataframe(self, *a, **kw): pass
    def table(self, *a, **kw): pass
    def plotly_chart(self, *a, **kw): pass
    def metric(self, *a, **kw): pass

    def cache_data(self, func=None, **kw):
        if func is None:
            def deco(f):
                f.clear = lambda: None
                return f
            return deco
        func.clear = lambda: None
        return func

    def cache_resource(self, func=None, **kw):
        return self.cache_data(func, **kw)


class FakePlotlyExpress(types.ModuleType):
    def __init__(self):
        super().__init__("plotly.express")

    def _fig(self, *a, **kw):
        return _FakeFig()

    bar = _fig = None  # placeholder, replaced below


class _FakeFig:
    def update_layout(self, *a, **kw): return self
    def add_hline(self, *a, **kw): return self
    def add_vline(self, *a, **kw): return self


def _make_plotly_express():
    mod = types.ModuleType("plotly.express")
    for name in ("bar", "line", "histogram", "scatter", "box", "area", "pie"):
        setattr(mod, name, lambda *a, **kw: _FakeFig())
    return mod


def install():
    """Install the fakes into sys.modules. Call before importing any page module."""
    st = FakeStreamlit()
    sys.modules["streamlit"] = st

    plotly = types.ModuleType("plotly")
    px = _make_plotly_express()
    plotly.express = px
    sys.modules["plotly"] = plotly
    sys.modules["plotly.express"] = px

    graph_objects = types.ModuleType("plotly.graph_objects")
    sys.modules["plotly.graph_objects"] = graph_objects

    return st
