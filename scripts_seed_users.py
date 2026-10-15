import csv
import hashlib
from pathlib import Path


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# تعریف کاربران پیش‌فرض با اطلاعات و پسوردهای اولیه
default_users = [
    {
        "username": "admin",
        "password_hash": hash_password("1234"),
        "role": "admin",
        "allowed_dcs": "all",
        "is_active": 1,
    },
    {
        "username": "editor_tehran",
        "password_hash": hash_password("editor123"),
        "role": "editor",
        "allowed_dcs": "1,2",  # فقط دسترسی به دیتاسنترهای 1 و 2
        "is_active": 1,
    },
    {
        "username": "viewer_all",
        "password_hash": hash_password("view123"),
        "role": "read_only",
        "allowed_dcs": "all",
        "is_active": 1,
    },
]

# نوشتن کاربران درون فایل users.csv
users_file = Path("users.csv")
fieldnames = ["username", "password_hash", "role", "allowed_dcs", "is_active"]

with users_file.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(default_users)

print("فایل users.csv با موفقیت و پسوردهای هش‌شده ساخته شد!")
