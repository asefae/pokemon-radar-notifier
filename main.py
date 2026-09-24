"""Entry point for the Pokémon radar notifier."""

from gui import RadarApp


def main() -> None:
    app = RadarApp()
    app.mainloop()


if __name__ == "__main__":
    main()
