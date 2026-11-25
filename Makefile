.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down build logs ps restart test verify clean env

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it does not exist yet
	@test -f .env || (cp .env.example .env && echo "Created .env - add your real keys to LLM_API_KEYS")

up: env ## Build and start the stack (backend :8000, frontend :3000)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  backend   http://localhost:8000/health"
	@echo "  frontend  http://localhost:3000"
	@echo ""

down: ## Stop the stack and remove the containers
	$(COMPOSE) down

build: ## Build both images without starting them
	$(COMPOSE) build

logs: ## Tail logs from both services
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

restart: ## Restart both services
	$(COMPOSE) restart

test: ## Run the backend test suite
	cd backend && python -m pytest

verify: ## Run the API contract check against BASE (default http://localhost:8000)
	bash scripts/verify.sh $(BASE)

clean: ## Stop the stack and delete the SQLite volume (destroys history)
	$(COMPOSE) down -v
