import logging
import os
from dataclasses import dataclass
from typing import Optional

import environs
from marshmallow.validate import Range

from pyslac.enums import Timers

logger = logging.getLogger(__name__)


@dataclass
class Config:
    slac_init_timeout: Optional[int] = None
    slac_atten_results_timeout: Optional[int] = None
    # New: allow overriding request/response timeouts for robustness
    slac_req_timeout: Optional[float] = None
    slac_resp_timeout: Optional[float] = None
    log_level: Optional[int] = None

    def load_envs(self, env_path: Optional[str] = None) -> None:
        """
        Tries to load the .env file containing all the project settings.
        If `env_path` is not specified, it will get the .env on the current
        working directory of the project
        Args:
            env_path (str): Absolute path to the location of the .env file
        """
        env = environs.Env(eager=False)
        if not env_path:
            env_path = os.getcwd() + "/.env"
        env.read_env(path=env_path)  # read .env file, if it exists

        # This timer is set in docker-compose.dev.yml, for merely debugging and dev
        # reasons
        self.slac_init_timeout = env.float(
            "SLAC_INIT_TIMEOUT", default=Timers.SLAC_INIT_TIMEOUT
        )
        # Clamp to ISO 15118-3 TT_EVSE_SLAC_init range [20s, 50s]
        try:
            if self.slac_init_timeout is not None:
                if self.slac_init_timeout < 20.0:
                    logger.warning(
                        "SLAC_INIT_TIMEOUT=%.3fs below spec minimum; clamping to 20s",
                        self.slac_init_timeout,
                    )
                    self.slac_init_timeout = 20.0
                elif self.slac_init_timeout > 50.0:
                    logger.warning(
                        "SLAC_INIT_TIMEOUT=%.3fs above spec maximum; clamping to 50s",
                        self.slac_init_timeout,
                    )
                    self.slac_init_timeout = 50.0
        except Exception:
            pass

        # A max value of 1050 is imposed to this env as the EV timeout value is
        # 1200 ms as described in [V2G3-A09-31] and we dont want to trigger it
        self.slac_atten_results_timeout = env.int(
            "ATTEN_RESULTS_TIMEOUT", default=None, validate=Range(max=1050)
        )

        # Optional overrides (seconds) for request/response waits used in a few
        # SLAC receive points. Defaults follow ISO15118-3 timers.
        try:
            self.slac_req_timeout = env.float("SLAC_REQ_TIMEOUT_S", default=None)
        except Exception:
            self.slac_req_timeout = None
        try:
            self.slac_resp_timeout = env.float("SLAC_RESP_TIMEOUT_S", default=None)
        except Exception:
            self.slac_resp_timeout = None

        self.log_level = env.str("LOG_LEVEL", default="INFO")

        env.seal()  # raise all errors at once, if any
