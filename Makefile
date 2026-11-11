.PHONY: help build up down restart logs ps test

help: ## Список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

build: ## Собрать образ
	docker compose build

up: ## Запустить сервис (ядро уже должно работать)
	docker compose up -d

down: ## Остановить сервис
	docker compose down

restart: down up ## Перезапустить

logs: ## Журнал сервиса
	docker compose logs -f

ps: ## Состояние контейнера
	docker compose ps

test: ## Прогнать проверки в контейнере
	docker build --target test -t mfc-test . && docker run --rm mfc-test
