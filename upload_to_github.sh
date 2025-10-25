#!/bin/bash
# Цей скрипт оновить віддалений репозиторій та завантажить код.

echo "Видалення старого remote origin..."
git remote remove origin

echo "Додавання нового remote origin..."
git remote add origin https://github.com/andriipushkar/scalp_v1a

echo "Перевірка нового remote..."
git remote -v

echo "Завантаження коду на GitHub..."
git push -u origin master --force

echo "Готово!"
