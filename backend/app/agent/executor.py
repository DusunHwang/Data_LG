"""smolagents LocalPythonExecutor 설정.

- ``AUTHORIZED_IMPORTS``: CodeAgent가 사용할 수 있는 패키지 화이트리스트.
- ``KOREAN_FONT_PREAMBLE``: matplotlib 한글 폰트를 보장하는 인라인 코드.
  CodeAgent의 실행 환경(LocalPythonExecutor)에서 import os/glob/matplotlib만
  허용해도 동작하도록 작성. ``graph/helpers.py``의 KOREAN_FONT_PREAMBLE과
  동일한 폰트 우선순위를 사용한다.
"""

from app.core.config import settings

AUTHORIZED_IMPORTS: list[str] = [
    # 데이터/수치
    "pandas", "pandas.*",
    "numpy", "numpy.*",
    "scipy", "scipy.*",
    # 시각화
    "matplotlib", "matplotlib.*",
    "seaborn", "seaborn.*",
    "plotly", "plotly.*",
    # ML
    "sklearn", "sklearn.*",
    "statsmodels", "statsmodels.*",
    "lightgbm",
    "xgboost",
    "catboost",
    "shap",
    "optuna", "optuna.*",
    # 표준 라이브러리
    "json",
    "math",
    "datetime",
    "itertools",
    "collections",
    "re",
    "io",
    "pathlib",
    "os",
    "os.path",
    "glob",
    "tempfile",
    "typing",
]


KOREAN_FONT_PREAMBLE = r"""
# ── 한글 폰트 설정 (smolagents LocalPythonExecutor) ──────────
import os as _os
import tempfile as _tempfile
_os.environ.setdefault("MPLCONFIGDIR", _os.path.join(_tempfile.gettempdir(), "matplotlib-cache"))
_os.makedirs(_os.environ["MPLCONFIGDIR"], exist_ok=True)
import glob as _glob
import matplotlib as _mpl
_mpl.use("Agg")
import matplotlib.font_manager as _fm

_candidates = _fm.findSystemFonts(fontext='ttf')
for _pat in ['/usr/share/fonts/**/*.ttf', '/usr/share/fonts/**/*.TTF',
             '/usr/local/share/fonts/**/*.ttf', '/root/.fonts/**/*.ttf']:
    _candidates.extend(_glob.glob(_pat, recursive=True))

def _pick_font(lst):
    pairs = [(f, _os.path.basename(f).lower()) for f in lst]
    g = [f for f, name in pairs if 'nanumgothic' in name or 'nanumbarungothic' in name]
    n = [f for f, name in pairs if 'nanum' in name]
    noto = [f for f, name in pairs if 'noto' in name and ('cjk' in name or 'kr' in name)]
    c = [f for f, name in pairs if 'cjk' in name or 'korean' in name or 'malgun' in name]
    return (g or n or noto or c or [None])[0]

_font_path = _pick_font(_candidates)
if _font_path is None:
    try:
        _fm._load_fontmanager(try_read_cache=False)
        _font_path = _pick_font(_fm.findSystemFonts(fontext='ttf'))
    except Exception:
        pass

if _font_path:
    try:
        _fm.fontManager.addfont(_font_path)
    except Exception:
        pass
    _font_name = _fm.FontProperties(fname=_font_path).get_name()
    _mpl.rcParams['font.sans-serif'] = [_font_name, 'DejaVu Sans']
    _mpl.rcParams['font.family'] = 'sans-serif'
_mpl.rcParams['axes.unicode_minus'] = False
# ─────────────────────────────────────────────────────────────
"""


def build_executor_preamble(work_dir: str | None = None) -> str:
    """LocalPythonExecutor 시작 시 1회 실행할 프리앰블 코드.

    work_dir이 주어지면 chdir + matplotlib 저장 위치 기본값 보장.
    """
    if not work_dir:
        return KOREAN_FONT_PREAMBLE
    return (
        KOREAN_FONT_PREAMBLE
        + f"\nimport os\nos.chdir({work_dir!r})\n"
    )


def build_executor_kwargs() -> dict:
    """CodeAgent(executor_kwargs=...)로 그대로 넘길 인자."""
    return {
        "max_print_outputs_length": settings.agent_executor_max_print_length,
    }
