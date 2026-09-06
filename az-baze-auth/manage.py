import getpass
import secrets
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import DB_PATH, SCHEMA, iso_now

GOLOVIN_EMAIL = "balda@inbox.ru"
GOLOVIN_NAME = "Головин"
ZUBACHEV_EMAIL = "zubachevr@gmail.com"
ZUBACHEV_NAME = "Зубачёв Роман Николаевич"


def main():
    print(f"База авторизации: {DB_PATH}")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    existing = conn.execute("SELECT email FROM users WHERE email IN (?,?)", (GOLOVIN_EMAIL, ZUBACHEV_EMAIL)).fetchall()
    if existing:
        print("Один или оба администратора уже существуют. Повторное создание остановлено.")
        for row in existing:
            print("-", row["email"])
        conn.close()
        return

    while True:
        password = getpass.getpass(f"Задайте пароль для {GOLOVIN_EMAIL} (не менее 10 символов): ")
        if len(password) < 10:
            print("Пароль слишком короткий.")
            continue
        password2 = getpass.getpass("Повторите пароль: ")
        if password != password2:
            print("Пароли не совпадают.")
            continue
        break

    temp_zubachev = secrets.token_urlsafe(10)
    now = iso_now()

    conn.execute(
        "INSERT INTO users(email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,1,0,?,?)",
        (GOLOVIN_EMAIL, GOLOVIN_NAME, generate_password_hash(password), 1, now, now),
    )
    conn.execute(
        "INSERT INTO users(email,full_name,password_hash,is_admin,active,must_change_password,created_at,updated_at) VALUES(?,?,?,?,1,1,?,?)",
        (ZUBACHEV_EMAIL, ZUBACHEV_NAME, generate_password_hash(temp_zubachev), 1, now, now),
    )
    conn.commit()
    conn.close()

    print("\nАдминистраторы созданы.")
    print(f"Ваш логин: {GOLOVIN_EMAIL}")
    print(f"Логин Зубачёва Р.Н.: {ZUBACHEV_EMAIL}")
    print(f"Временный пароль Зубачёва Р.Н.: {temp_zubachev}")
    print("Сохраните/передайте временный пароль сейчас: повторно показать его будет невозможно.")
    print("При первом входе Зубачёв Р.Н. будет обязан задать свой пароль.")


if __name__ == "__main__":
    main()
