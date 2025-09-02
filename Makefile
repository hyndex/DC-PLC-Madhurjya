PY := python3

.PHONY: smoke smoke-mid smoke-all

smoke:
	@$(PY) scripts/secc_timeout_smoke.py

smoke-mid:
	@$(PY) scripts/secc_mid_timeout_smoke.py

smoke-all: smoke smoke-mid
	@echo "All smoke tests completed"

