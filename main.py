import threading

from app.bot import GridBot
from app.dashboard import init_dashboard
from app.config import Config


def main():
    bot = GridBot()
    app = init_dashboard(bot)

    t = threading.Thread(target=bot.run_forever, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=Config.PORT)


if __name__ == "__main__":
    main()
