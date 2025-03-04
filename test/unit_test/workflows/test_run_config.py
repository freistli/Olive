# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# --------------------------------------------------------------------------

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from olive.data.config import DataConfig
from olive.workflows.run.config import INPUT_MODEL_DATA_CONFIG, RunConfig

# pylint: disable=attribute-defined-outside-init, unsubscriptable-object


class TestRunConfig:
    # like: Systems/Evaluation/Model and etc.
    @pytest.fixture(autouse=True)
    def setup(self):
        self.user_script_config_file = Path(__file__).parent / "mock_data" / "user_script.json"

    @pytest.mark.parametrize(
        "config_file",
        [
            Path(__file__).parent / "mock_data" / "transformer_dataset.json",
            Path(__file__).parent / "mock_data" / "only_transformer_dataset.json",
            Path(__file__).parent / "mock_data" / "ner_task_dataset.json",
            Path(__file__).parent / "mock_data" / "text_generation_dataset.json",
            Path(__file__).parent / "mock_data" / "text_generation_dataset_random.json",
        ],
    )
    def test_dataset_config_file(self, config_file):
        run_config = RunConfig.parse_file(config_file)
        for dc in run_config.data_configs.values():
            dc.to_data_container().create_dataloader(data_root_path=None)

    @pytest.mark.parametrize("system", ["local_system", "azureml_system"])
    def test_user_script_config(self, system):
        with self.user_script_config_file.open() as f:
            user_script_config = json.load(f)

        user_script_config["engine"]["host"] = system
        user_script_config["engine"]["target"] = system
        config = RunConfig.parse_obj(user_script_config)
        for metric in config.evaluators["common_evaluator"].metrics:
            assert metric.user_config.data_dir.get_path().startswith("azureml://")

    def test_config_without_azureml_config(self):
        with self.user_script_config_file.open() as f:
            user_script_config = json.load(f)

        user_script_config.pop("azureml_client")
        with pytest.raises(ValueError) as e:  # noqa: PT011
            RunConfig.parse_obj(user_script_config)
        assert "AzureML client config is required for AzureML system" in str(e.value)

    @pytest.fixture()
    def mock_aml_credentials(self):
        # we need to mock all the credentials because the default credential will get tokens from all of them
        self.mocked_env_credentials = patch("azure.identity._credentials.default.EnvironmentCredential").start()
        self.mocked_managed_identity_credentials = patch(
            "azure.identity._credentials.default.ManagedIdentityCredential"
        ).start()
        self.mocked_shared_token_cache_credentials = patch(
            "azure.identity._credentials.default.SharedTokenCacheCredential"
        ).start()
        self.mocked_azure_cli_credentials = patch("azure.identity._credentials.default.AzureCliCredential").start()
        self.mocked_azure_powershell_credentials = patch(
            "azure.identity._credentials.default.AzurePowerShellCredential"
        ).start()
        self.mocked_interactive_browser_credentials = patch(
            "azure.identity._credentials.default.InteractiveBrowserCredential"
        ).start()
        yield
        patch.stopall()

    @pytest.mark.usefixtures("mock_aml_credentials")
    @pytest.mark.parametrize(
        "default_auth_params",
        [
            (None, (1, 1, 1, 1, 1, 0)),
            (
                {"exclude_environment_credential": True, "exclude_managed_identity_credential": False},
                (0, 1, 1, 1, 1, 0),
            ),
            ({"exclude_environment_credential": True, "exclude_managed_identity_credential": True}, (0, 0, 1, 1, 1, 0)),
        ],
    )
    def test_config_with_azureml_default_auth_params(self, default_auth_params):
        """default_auth_params[0] is a dict of the parameters to be passed to DefaultAzureCredential.

        default_auth_params[1] is a tuple of the number of times each credential is called.
        the order is totally same with that in DefaultAzureCredential where the credentials
        are called sequentially until one of them succeeds:
            EnvironmentCredential -> ManagedIdentityCredential -> SharedTokenCacheCredential
            -> AzureCliCredential -> AzurePowerShellCredential -> InteractiveBrowserCredential
        https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential?view=azure-python
        """
        with self.user_script_config_file.open() as f:
            user_script_config = json.load(f)

        user_script_config["azureml_client"]["default_auth_params"] = default_auth_params[0]
        config = RunConfig.parse_obj(user_script_config)
        config.azureml_client.create_client()
        assert (
            self.mocked_env_credentials.call_count,
            self.mocked_managed_identity_credentials.call_count,
            self.mocked_shared_token_cache_credentials.call_count,
            self.mocked_azure_cli_credentials.call_count,
            self.mocked_azure_powershell_credentials.call_count,
            self.mocked_interactive_browser_credentials.call_count,
        ) == default_auth_params[1]

    @patch("azure.identity.DefaultAzureCredential")
    @patch("azure.identity.InteractiveBrowserCredential")
    def test_config_with_failed_azureml_default_auth(self, mocked_interactive_login, mocked_default_azure_credential):
        mocked_default_azure_credential.side_effect = Exception("mock error")
        with self.user_script_config_file.open() as f:
            user_script_config = json.load(f)
        config = RunConfig.parse_obj(user_script_config)
        config.azureml_client.create_client()
        assert mocked_interactive_login.call_count == 1

    def test_readymade_system(self):
        readymade_config_file = Path(__file__).parent / "mock_data" / "readymade_system.json"
        with readymade_config_file.open() as f:
            user_script_config = json.load(f)

        cfg = RunConfig.parse_obj(user_script_config)
        assert cfg.engine.target.config.accelerators == ["GPU"]

    def test_default_engine(self):
        default_engine_config_file = Path(__file__).parent / "mock_data" / "default_engine.json"
        run_config = RunConfig.parse_file(default_engine_config_file)
        assert run_config.evaluators is None
        assert run_config.engine.host is None
        assert run_config.engine.target is None


class TestDataConfigValidation:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.template = {
            "input_model": {
                "type": "PyTorchModel",
                "config": {
                    "hf_config": {
                        "model_name": "dummy_model",
                        "task": "dummy_task",
                        "dataset": {"name": "dummy_dataset"},
                    }
                },
            },
            "data_configs": {
                "dummy_data_config2": {
                    "name": "dummy_data_config2",
                    "type": "HuggingfaceContainer",
                    "params_config": {
                        "model_name": "dummy_model2",
                        "task": "dummy_task2",
                        "data_name": "dummy_dataset2",
                    },
                }
            },
            "passes": {"tuning": {"type": "OrtPerfTuning"}},
            "engine": {"evaluate_input_model": False},
        }

    @pytest.mark.parametrize(
        ("model_name", "task", "expected_model_name", "expected_task"),
        [
            ("dummy_model2", "dummy_task2", "dummy_model2", "dummy_task2"),  # no auto insert
            ("dummy_model2", None, "dummy_model2", "dummy_task"),  # auto insert task
            (None, "dummy_task2", "dummy_model", "dummy_task2"),  # auto insert model_name
            (None, None, "dummy_model", "dummy_task"),  # auto insert model_name and task
        ],
    )
    def test_auto_insert_model_name_and_task(self, model_name, task, expected_model_name, expected_task):
        config_dict = self.template.copy()
        config_dict["data_configs"]["dummy_data_config2"]["params_config"] = {
            "model_name": model_name,
            "task": task,
            "data_name": "dummy_dataset2",
        }

        run_config = RunConfig.parse_obj(config_dict)
        assert run_config.data_configs["dummy_data_config2"].params_config["model_name"] == expected_model_name
        assert run_config.data_configs["dummy_data_config2"].params_config["task"] == expected_task

    # works similarly for trust_remote_args
    @pytest.mark.parametrize(
        ("has_loading_args", "trust_remote_code", "data_config_trust_remote_code", "expected_trust_remote_code"),
        [
            (False, None, None, None),
            (False, None, True, True),
            (True, True, None, True),
            (True, None, None, None),
            (True, None, True, True),
            (True, None, False, False),
            (True, True, False, False),
            (True, False, True, True),
        ],
    )
    def test_auto_insert_trust_remote_code(
        self, has_loading_args, trust_remote_code, data_config_trust_remote_code, expected_trust_remote_code
    ):
        config_dict = self.template.copy()
        if has_loading_args:
            config_dict["input_model"]["config"]["hf_config"]["from_pretrained_args"] = {
                "trust_remote_code": trust_remote_code
            }
        if data_config_trust_remote_code is not None:
            config_dict["data_configs"]["dummy_data_config2"]["params_config"][
                "trust_remote_code"
            ] = data_config_trust_remote_code

        run_config = RunConfig.parse_obj(config_dict)
        if expected_trust_remote_code is None:
            assert "trust_remote_code" not in run_config.data_configs["dummy_data_config2"].params_config
        else:
            assert (
                run_config.data_configs["dummy_data_config2"].params_config["trust_remote_code"]
                == expected_trust_remote_code
            )

    @pytest.mark.parametrize(
        "data_config_str",
        [None, INPUT_MODEL_DATA_CONFIG, "dummy_data_config2"],
    )
    def test_str_to_data_config(self, data_config_str):
        config_dict = self.template.copy()
        config_dict["passes"]["tuning"]["config"] = {"data_config": data_config_str}

        run_config = RunConfig.parse_obj(config_dict)
        pass_data_config = run_config.passes["tuning"].config["data_config"]
        if data_config_str is None:
            assert pass_data_config is None
        else:
            assert isinstance(pass_data_config, DataConfig)
            assert pass_data_config.name == data_config_str


class TestPassConfigValidation:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.template = {
            "input_model": {
                "type": "OnnxModel",
                "config": {"hf_config": {"model_name": "dummy_model"}},
            },
            "passes": {"tuning": {"type": "IncQuantization", "config": {}}},
            "engine": {"evaluate_input_model": False},
        }

    @pytest.mark.parametrize(
        ("search_strategy", "disable_search", "approach", "is_valid"),
        [
            (None, None, None, True),
            (None, None, "SEARCHABLE_VALUES", False),
            (None, False, "SEARCHABLE_VALUES", False),
            (None, None, "dummy_approach", True),
            (None, True, "dummy_approach", True),
            (
                {"execution_order": "joint", "search_algorithm": "exhaustive"},
                None,
                "SEARCHABLE_VALUES",
                True,
            ),
        ],
    )
    def test_pass_config_(self, search_strategy, disable_search, approach, is_valid):
        config_dict = self.template.copy()
        config_dict["engine"]["search_strategy"] = search_strategy
        config_dict["passes"]["tuning"]["disable_search"] = disable_search
        config_dict["passes"]["tuning"]["config"] = {"approach": approach}
        if not is_valid:
            with pytest.raises(ValueError):  # noqa: PT011
                RunConfig.parse_obj(config_dict)
        else:
            config = RunConfig.parse_obj(config_dict)
            assert config.passes["tuning"].config["approach"] == approach
