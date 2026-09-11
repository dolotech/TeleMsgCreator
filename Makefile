PY ?= .venv/bin/python
PIP ?= $(PY) -m pip

.PHONY: help install dev test test-live lint fmt check ship package-win package-mac package preview serve dry-run scheduler docker clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## 安装到当前 Python 环境
	$(PIP) install -e ".[web,media]"

dev: ## 安装开发依赖（含 pytest/ruff）
	$(PIP) install -e ".[dev,web,media]"

test: ## 跑全部测试
	$(PY) -m pytest -q

test-live: ## 用真实 Bot Token 跑联调测试（只读 + 可选发一条再撤回）
	TELEMSG_LIVE_TEST=1 $(PY) -m pytest tests/test_live.py -v -s

lint: ## 静态检查
	$(PY) -m ruff check src tests

check: ## 推送前的全套检查（工作区、敏感信息、lint、测试、远端同步）
	scripts/ship.sh --dry-run

ship: ## 规范化推送（检查全过才推）
	scripts/ship.sh

fmt: ## 自动格式化
	$(PY) -m ruff check --fix src tests

serve: ## 启动 Web 编辑器
	$(PY) -m telemsg serve

scheduler: ## 启动计划任务守护进程
	$(PY) -m telemsg run-scheduler

dry-run: ## 用内置示例走一遍干跑
	$(PY) -m telemsg example > /tmp/telemsg-example.json
	$(PY) -m telemsg send --json /tmp/telemsg-example.json --dry-run

docker: ## 构建镜像
	docker build -t telemsg:1.0.0 .

package-win: ## 打包 Windows 绿色免安装版（可在 macOS 上直接执行）
	$(PY) scripts/build_release.py --target windows

package-mac: ## 打包 macOS 版（需要 pyinstaller，且必须在 macOS 上执行）
	$(PY) scripts/build_release.py --target macos

package: ## 打包当前系统可产出的全部目标
	$(PY) scripts/build_release.py --target all

clean: ## 清理本地缓存（保留 data/）
	rm -rf .pytest_cache .ruff_cache **/__pycache__ build dist *.egg-info
