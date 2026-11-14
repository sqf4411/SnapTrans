# 双击运行（无控制台窗口）：pythonw run.pyw
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from snaptrans.app import main  # noqa: E402

sys.exit(main())
