from .i18n import load_saved_language

load_saved_language()  # before the window module: its texts are read on import

from .app import main  # noqa: E402


raise SystemExit(main())
