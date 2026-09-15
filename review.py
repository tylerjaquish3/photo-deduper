import sys

from review_app import create_app


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Review and resolve duplicate photo groups.")
    parser.add_argument("root", help="Directory that was scanned")
    args = parser.parse_args(argv)

    app = create_app(args.root)
    app.run(debug=False, port=5000)
    return 0


if __name__ == "__main__":
    sys.exit(main())
