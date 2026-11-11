# Проверка DEBUG MODE

2026-11-09. Требования выведены из запроса: включаемое накопление результатов
успешной/неудачной работы и повторно используемых кейсов для helper suite.

## RED → GREEN

Команда: `python -X utf8 tests/test_debug.py`.

- До реализации: `TypeError: McpServer.__init__() got an unexpected keyword argument 'debug_dir'`.
- Проверка секретов выявила утечку в обычный аудит: `AssertionError: input-password`.
  При DEBUG аудит теперь использует ту же очистку, что и журнал.
- Fault injection очистки выявил изменение результата инструмента: `ValueError: test-password`.
  Сбои диагностики теперь изолированы от результата операции.
- После исправлений: **12/12 DEBUG-тестов прошли**. Регрессии: **31/31** в `tests/test_ips.py`.

## Гарантии

| Проверяется | Доказательство в `tests/test_debug.py` |
|---|---|
| Флаг из config/init, приоритет env, выключенный режим, stdio из другого cwd | `test_debug_config_and_init_support_flag_and_env_priority`, `test_stdio_flag_controls_debug_files_tools_and_instructions_from_other_cwd` |
| Полные ответы, HTTP-контекст, исходный ответ клиенту | `test_debug_records_full_results_and_http_context_without_changing_response` |
| Ошибки и секреты в DEBUG/аудите | `test_debug_records_failures_and_redacts_secrets_in_inputs_and_details` |
| Auth без тел, ретраи и сетевые ошибки | `test_debug_traces_retries_and_omits_authentication_bodies`, `test_debug_network_errors_and_broken_http_recorder_do_not_change_retry_policy` |
| Markdown с выводами и существующими подтверждениями | `test_debug_cases_preserve_summary_and_real_evidence` |
| Preview/unknown/partial/rejected не превращаются в успех | `test_debug_rejects_fabricated_evidence_and_preview_as_success`, `test_debug_keeps_partial_and_rejected_write_outcomes` |
| Сбой файла/очистки не вызывает повтор IPS; не создаёт ложный успех сохранения | `test_debug_io_failure_never_repeats_or_changes_a_successful_mutation`, `test_debug_redaction_failure_does_not_change_successful_tool_result`, `test_debug_case_write_failure_reports_error_and_preserves_existing_file` |

## Дополнительные проверки

- Стандартный `trace.Trace(count=True, trace=False)` с последовательным запуском
  обоих файлов через `runpy.run_path(..., run_name="__main__")`: покрытие строк
  **`ips_mcp.debug` — 93,8%** (144 исполняемых строки), `ips_mcp.ips` — 88,1%,
  `ips_mcp.server` — 75,1%. Подпроцессы stdio в это покрытие не включены.
- `python -m ruff check src/ips_mcp/debug.py tests/test_debug.py --ignore E402` — PASS.
  E402 исключён для standalone-тестов, которые добавляют `src` в `sys.path` перед импортом.
- `python -m ruff check src/ips_mcp --select E9,F63,F7,F82` — PASS.
- Синтаксис трёх модулей и нового теста проверен через `ast.parse(..., feature_version=(3, 10))` — PASS.
- `git -c core.safecrlf=false diff --check` — PASS.
- `config.json` и `config.example.json` валидны, `IPS_DEBUG_MODE` по умолчанию `false`.
- Установленный `ips_mcp.server` разрешается в `D:\IPS\IPS-MCP\src\ips_mcp\server.py`.

Live IPS не использовался: HTTP проверен через `httpx.MockTransport`, stdio — отдельными
процессами без запросов к IPS. Поведение помощника при формулировке выводов не является
гарантией unit-тестов: автоматический журнал работает независимо от итогового кейса.
Для включения в текущем подключении нужен `IPS_DEBUG_MODE=true` и перезапуск MCP.
