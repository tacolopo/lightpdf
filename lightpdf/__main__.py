"""Allow running as: python -m lightpdf [file.pdf]"""

from .app import LightPDFApp
import sys


def main():
    filepath = None
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        filepath = args[0]
    app = LightPDFApp(filepath=filepath)
    sys.exit(app.run(sys.argv[:1]))


if __name__ == "__main__":
    main()
