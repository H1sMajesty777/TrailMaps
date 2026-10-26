# TrailMaps — Backend

REST API сервиса пеших маршрутов (ТЗ: корневой `README.md`). FastAPI · SQLAlchemy 2 (async) · GeoAlchemy2 ·
PostgreSQL + PostGIS · Redis · OSRM · JWT · Docker Compose · Nginx.

## Быстрый старт

### Весь стек в Docker (dev: HTTP, Swagger включён)
```bash
cp .env.example .env            # заполните секреты: openssl rand -hex 24
# для dev удобно: HTTP_PORT=8080
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
# API:     http://localhost:8080/api/health/ready
# Swagger: http://localhost:8080/api/docs   (только ENVIRONMENT=dev)
```
Первый запуск OSRM скачивает карту (`OSRM_PBF_URL`, по умолчанию крошечный Monaco) и строит граф для профиля
`foot`. **Укажите свой регион** с https://download.geofabrik.de — без этого построение маршрутов за пределами
Монако вернёт `422 route_not_found`. Остальное API работает и без OSRM.

### Production
`docker compose up -d --build`. Наружу публикуются только порты Nginx; Postgres, Redis, OSRM и API живут в
изолированной сети `internal` без доступа в интернет. Сборка React кладётся в том `frontend_dist` (Nginx раздаёт её как статику).

HTTPS (Let's Encrypt), первый выпуск:
```bash
scripts/selfsigned-cert.sh example.com          # временный сертификат, чтобы nginx смог стартовать
docker compose up -d                            # SERVER_NAME=example.com в .env
docker compose run --rm --entrypoint sh certbot -c \
  "rm -rf /etc/letsencrypt/live/example.com && certbot certonly --webroot -w /var/www/certbot -d example.com --agree-tos -m you@example.com -n"
docker compose exec nginx nginx -s reload
docker compose --profile tls up -d certbot      # автопродление (после продления делайте nginx -s reload, например cron-ом)
```

### Локально без Docker для приложения
```bash
docker compose -f docker-compose.test.yml up -d        # PostGIS + Redis для тестов
cd backend && pip install -r requirements-dev.txt
pytest --cov=app                                       # миграции применяются самим тестовым прогоном
ruff check .
```
Тесты **стирают данные** (`TRUNCATE`), поэтому отказываются работать, если имя БД не содержит `test`.

## Архитектура
```
app/
  main.py            фабрика приложения, lifespan (engine, Redis, OSRM-клиент, воркер сброса просмотров)
  core/              config, errors (единый формат ошибок), security (bcrypt/JWT), middleware, ratelimit, geo, images
  db/                модели (1:1 со схемой из ТЗ), сессии
  schemas/           Pydantic-контракты
  services/          бизнес-логика (auth, routes, views, complaints, admin, stats, osrm, avatars)
  api/endpoints/     тонкие HTTP-обработчики
alembic/             миграции (в т.ч. сид тегов: еда, развлечения, прогулка, исторический)
tests/               unit/ (чистая логика) и api/ (интеграция: реальные PostGIS и Redis, OSRM — фейк)
```

## API
Все эндпоинты из раздела 6 ТЗ + дополнения (выделены): 

| Область | Эндпоинты |
|---|---|
| Auth | `POST /api/auth/register`, `login`, **`refresh`**, `logout`, `GET /api/auth/me` |
| Users | `GET/PUT /api/users/me`, `POST /api/users/me/avatar`, `GET /api/users/{id}`, `GET /api/users/me/routes` |
| Routes | `POST /api/routes/build`, `POST/GET /api/routes`, `GET/PUT/DELETE /api/routes/{id}`, `POST/DELETE /api/routes/{id}/like` |
| Tags | `GET/POST /api/tags`, **`PUT/DELETE /api/tags/{id}`** |
| Complaints | `POST/GET /api/complaints`, `PUT /api/complaints/{id}` |
| Admin | `GET /api/admin/users`, `PUT /api/admin/users/{id}`, **`PUT /api/admin/routes/{id}/visibility`**, `GET /api/admin/stats`, `/stats/users`, `/stats/views` |
| Ops | **`GET /api/health/live`**, **`/api/health/ready`** |

Формат ошибок единый: `{"error": {"code", "message", "details"}, "request_id"}`; `request_id` дублируется в заголовке `X-Request-ID`.
Списки пагинируются: `{"items", "total", "page", "limit", "pages"}`.

### Фильтры `GET /api/routes`
`lat`, `lng` (вместе), `radius` (**метры**, по умолчанию 5000, макс. 100000), `duration_min/max` (**секунды**),
`tags` (имена, повторяемый параметр) + `tags_match=any|all`, `sort=popular|likes|views|new|duration|distance`, `page`, `limit≤100`.
«Время до маршрута» из ТЗ клиент переводит в метры (≈ 5 км/ч). Маршрут попадает в выдачу, если его линия
**хоть частично** в радиусе (`ST_DWithin` по `GEOGRAPHY`, GiST-индекс). `popular` = `likes*5 + views`.
В карточках геометрия упрощена (Дуглас–Пекер, ~11 м), в `GET /api/routes/{id}` — полная.

## Принятые решения (ТЗ не уточняло)
* **Роли и доступ.** Анонимно доступны список/карточка маршрутов и теги; всё остальное — по токену. Менеджер и админ видят любые
  маршруты; править/удалять чужие может только админ. Скрывает маршрут только админ (`/admin/routes/{id}/visibility` или `hide_route` в
  `PUT /complaints/{id}`). Теги создаёт менеджер/админ, правит/удаляет админ. Общая статистика — админ; `stats/users` и `stats/views` — менеджер и админ.
  Роль читается из БД на каждом запросе: блокировка и смена роли действуют мгновенно.
* **JWT.** Access 15 мин, refresh 30 дней, ротация refresh (одноразовый, атомарный `SET NX` в Redis), blacklist по `jti` при logout.
  Если Redis недоступен — аутентификация *закрывается* (503), rate limit — *открывается* (доступность важнее).
* **Пароли.** bcrypt (12 раундов, в тредпуле), ≥8 символов, буква+цифра, ≤72 байт (ограничение bcrypt). Одинаковый ответ и время для
  «нет пользователя»/«неверный пароль». Блокировка входа: 5 неудач на пару IP+email → 429 на 15 минут.
* **Rate limiting.** Nginx (`limit_req`) + Redis: глобально на IP, жёстче на auth, отдельно на «дорогие» операции (на пользователя).
* **Геометрия считает сервер.** `duration/distance/route_geometry` клиентом не принимаются: сервер сам ходит в OSRM (кеш в Redis 1 ч).
  Лимиты: 2–25 точек, 10 м … 200 км.
* **Просмотры.** Событие пишется в Redis (BackgroundTask), дедупликация на 30 минут по пользователю/IP, автор не накручивает,
  скрытые/приватные не считаются. Раз в 30 с (и при остановке) батч уходит в `route_views` + `routes.views_count`. Счётчик в БД отстаёт на ≤30 с.
* **Лайки** идемпотентны (повторный POST/DELETE ничего не ломает), счётчик обновляется в той же транзакции; конкурентные запросы безопасны.
* **Кеш списка маршрутов** (60 с) инвалидируется счётчиком версии при любой мутации (создание, правка, удаление, лайк, скрытие).
* **Жалобы.** Одна открытая жалоба на пару (пользователь, маршрут) — частичный уникальный индекс; на свой маршрут жаловаться нельзя.
* **Аватары.** Проверка Pillow (JPEG/PNG/WEBP, ≤5 МБ, ≤25 Мп), EXIF вырезается, квадрат 512×512, WEBP; старый файл удаляется. В БД — относительный URL `/uploads/avatars/…`.
* **Схема БД** — как в ТЗ (типы, FK, каскады, индексы). Отличия: `TIMESTAMPTZ` вместо `TIMESTAMP`; уникальность `username` без учёта регистра;
  `CHECK` на роли/статусы/счётчики; явные B-tree индексы по `users.email/username` не нужны (их уже создают UNIQUE-ограничения).
* **Первый админ** создаётся при старте из `BOOTSTRAP_ADMIN_*` (идемпотентно).
* **Prod-защита конфигурации:** при `ENVIRONMENT=production|staging` приложение не стартует со слабым `JWT_SECRET` или `CORS_ORIGINS=*`. Swagger — только в `dev`.

## CI
* `.github/workflows/ci.yml` — на **каждый push** и PR: `ruff` → `pytest` (сервисы PostGIS 16 + Redis 7, покрытие ≥ 75 %) → сборка образа + smoke-тест всего compose-стека
  через Nginx (регистрация/логин/me/теги) → информационный `pip-audit`.
* `.github/dependabot.yml` — еженедельные обновления зависимостей, образа и actions.
* `.gitlab-ci.yml` — зеркало для GitLab из ТЗ (lint/test/build/push; deploy — заглушка, нужны ваши серверы).

## Что не входит / дальше
Деплой на серверы, фронтенд, ограничение доступа к Prometheus-метрикам (метрик пока нет), аудит-лог действий модераторов, подтверждение e-mail и сброс пароля (в ТЗ нет).
