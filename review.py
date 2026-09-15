import sys

from review_app import create_app


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Review and resolve duplicate photo groups.")
    parser.add_argument("root", help="Directory that was scanned")
    parser.add_argument(
        "--port",
        type=int,
        default=5151,
        help="Port to run the review server on (default: 5151, since 5000 collides "
        "with macOS AirPlay Receiver)",
    )
    args = parser.parse_args(argv)

    app = create_app(args.root)
    app.run(debug=False, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
