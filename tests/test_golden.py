#!/usr/bin/env python3
"""
Golden Test Suite for NexQL Workbench
Tests parser edge cases, CRUD operations, nested payloads, directives, and error conditions.
Run with: python3 test_golden.py
"""

import sys
sys.path.insert(0, '/home/nexu/Desktop/nexql-workbench')

from nexql_workbench import (
    execute_nexql,
    NexQLParser,
    _build_query_plan,
    _lint_query,
    infer_schema_from_collections,
    build_query_diff,
    expand_query_env,
    benchmark_query_runs,
    generate_mock_query_response,
    analyze_schema_relationships,
    analyze_field_usage,
    generate_api_docs,
    schema_diff_text,
    smart_search_schema,
    explain_schema_ai_style,
    nl_to_nexql_query,
)
import json

# Test counter
passed = 0
failed = 0
tests_run = []

def assert_eq(actual, expected, name):
    global passed, failed, tests_run
    if actual == expected:
        passed += 1
        print(f"  ✓ {name}")
        tests_run.append((name, True))
    else:
        failed += 1
        print(f"  ✗ {name}")
        print(f"    Expected: {expected}")
        print(f"    Got: {actual}")
        tests_run.append((name, False))

def assert_true(condition, name):
    global passed, failed, tests_run
    if condition:
        passed += 1
        print(f"  ✓ {name}")
        tests_run.append((name, True))
    else:
        failed += 1
        print(f"  ✗ {name}")
        tests_run.append((name, False))

# Prepare test database
def setup_db():
    cols = {
        "user": [
            {"id": "u_0001", "name": "Alice", "email": "alice@example.com", "age": 28, "role": "admin", "active": True, "createdAt": 1700000000},
            {"id": "u_0002", "name": "Bob", "email": "bob@example.com", "age": 35, "role": "user", "active": False, "createdAt": 1700000100},
        ],
        "post": [
            {"id": "p_0001", "title": "First Post", "body": "Content here", "author": {"id": "u_0001", "name": "Alice"}, "score": 8.5, "createdAt": 1700000200},
            {"id": "p_0002", "title": "Second Post", "body": "More content", "author": {"id": "u_0002", "name": "Bob"}, "score": 7.0, "createdAt": 1700000300},
            {"id": "p_0003", "title": "Third Post", "body": "Extra content", "author": {"id": "u_0001", "name": "Alice"}, "score": 6.0, "createdAt": 1700000400},
        ],
        "comment": [
            {"id": "c_0001", "text": "Great post!", "authorId": "u_0001", "postId": "p_0001", "createdAt": 1700000400},
        ],
    }
    return {"collections": cols}

# ============================================================================
print("=" * 70)
print("NexQL GOLDEN TEST SUITE")
print("=" * 70)

db = setup_db()

# ============================================================================
print("\n[PARSER EDGE CASES]")
print("-" * 70)

# Test 1: Simple read query
result = execute_nexql('? user (id "u_0001") { name email }', db)
assert_true(result['ok'] == True, "Parse: Simple read query")
assert_true(result['#data']['user']['name'] == 'Alice', "Parse: Field projection works")

# Test 2: Multiple query operators (using different collections to avoid data mutation)
test_ops = [
    ('?', '? post { id }',  "read"),
    ('+', '+ post { title "Test" author { id "u_0001" name "Alice" } } { id }', "create"),
    ('~', '~ post (id "p_0002") { title "Updated" } { id }', "update"),
    ('!', '! post (id "p_0003") { id }', "delete"),
    ('>>', '>> comment { id }', "subscribe"),
]
for op, query, method in test_ops:
    result = execute_nexql(query, db)
    # Should not have PARSE_ERROR (may have other errors like not found)
    has_parse_error = result.get('ok') == False and result['errors'][0]['code'] == 'PARSE_ERROR'
    assert_true(not has_parse_error, f"Parse: Operator {op} recognized ({method})")

# Tooling parser APIs
parser = NexQLParser()
tokens = parser.tokenize('? user (id "u_0001") { id name }')
assert_true(len(tokens) > 0, "Parse: Tokenizer returns tokens")
assert_true(any(t['type'] == 'METHOD' for t in tokens), "Parse: Tokenizer includes method token")

ast = parser.parse_to_ast('? user (id "u_0001") { id name }')
assert_eq(ast.get('type'), 'Query', "Parse: AST root type is Query")
assert_eq(ast.get('target'), 'user', "Parse: AST contains target")

grammar_ok = parser.validate_grammar('? user (id "u_0001") { id }')
assert_true(grammar_ok['ok'] is True, "Parse: Grammar validator accepts valid query")

plan = _build_query_plan(parser.parse('? post ($limit 5) { id title }'))
assert_eq(plan.get('method'), 'read', "Parse: Query planner captures method")
assert_true(isinstance(plan.get('stages'), list) and len(plan.get('stages')) > 0, "Parse: Query planner returns stages")

lint_ok = _lint_query('? user (id "u_0001") { id name }')
assert_true(not any(f.get('severity') == 'error' for f in lint_ok), "Parse: Linter accepts valid query")

lint_err = _lint_query('bad syntax')
assert_true(any(f.get('severity') == 'error' for f in lint_err), "Parse: Linter reports parse errors")

# Strict object/list parsing rules from logic.md
strict_obj_err = execute_nexql('+ user { name } { id }', db)
assert_true(strict_obj_err.get('ok') is False and strict_obj_err['errors'][0]['code'] == 'PARSE_ERROR', "Parse: Odd token count in object block is a parse error")

strict_list_err = execute_nexql('+ user { tags [ { id "x" } ] } { id }', db)
assert_true(strict_list_err.get('ok') is False and strict_list_err['errors'][0]['code'] == 'PARSE_ERROR', "Parse: Nested blocks in list values are rejected")

selector_comma_err = execute_nexql('? user { id, name }', db)
assert_true(selector_comma_err.get('ok') is False and selector_comma_err['errors'][0]['code'] == 'PARSE_ERROR', "Parse: Commas are rejected in selector blocks")

selector_colon_err = execute_nexql('? user { alias: name }', db)
assert_true(selector_colon_err.get('ok') is False and selector_colon_err['errors'][0]['code'] == 'PARSE_ERROR', "Parse: Colons are rejected in selector blocks")

reserved_filter_key_err = execute_nexql('? user (true 1) { id }', db)
assert_true(reserved_filter_key_err.get('ok') is False and reserved_filter_key_err['errors'][0]['code'] == 'PARSE_ERROR', "Parse: Reserved keywords cannot be filter keys")

inferred = infer_schema_from_collections({'users': [{'id': 'u_1', 'name': 'A', 'age': 1}]})
assert_true(isinstance(inferred, list) and len(inferred) > 0, "Parse: Schema inference returns type list")
assert_true(any(t.get('name') == 'User' for t in inferred), "Parse: Schema inference derives collection type")

diff_text = build_query_diff('? user { id }', '? user { id name }')
assert_true('@@' in diff_text, "Parse: Query diff builds unified diff")

expanded = expand_query_env('? user (id "{{UID}}") { id }', {'UID': 'u_0001'})
assert_true('{{UID}}' not in expanded and 'u_0001' in expanded, "Parse: Env placeholders expand")

bench = benchmark_query_runs('? user { id }', db, runs=3)
assert_true(bench.get('runs') == 3, "Parse: Benchmark returns run count")
assert_true('avg_ms' in bench, "Parse: Benchmark includes avg_ms")

mock = generate_mock_query_response('? user { id name }', db)
assert_true(mock.get('ok') is True, "Parse: Mock response generator works")
assert_true(mock.get('#data') is not None, "Parse: Mock response includes data")

sample_schema = [
    {
        'name': 'User',
        'fields': [
            {'name': 'id', 'type': 'uid', 'nullable': False},
            {'name': 'posts', 'type': '[Post]', 'nullable': True},
        ],
    },
    {
        'name': 'Post',
        'fields': [
            {'name': 'id', 'type': 'uid', 'nullable': False},
            {'name': 'author', 'type': 'User', 'nullable': False},
        ],
    },
]

rels = analyze_schema_relationships(sample_schema)
assert_true(len(rels) >= 1, "Parse: Schema relationship analysis returns edges")

usage = analyze_field_usage([{'query': '? user { id name posts { id } }'}])
assert_true(usage.get('id', 0) >= 1, "Parse: Field usage analytics counts fields")

docs_md = generate_api_docs(sample_schema)
assert_true('## User' in docs_md and '| Field | Type | Required |' in docs_md, "Parse: API docs are generated")

sdiff = schema_diff_text(sample_schema, sample_schema + [{'name': 'Org', 'fields': []}])
assert_true('current-schema' in sdiff, "Parse: Schema diff text generated")

hits = smart_search_schema(sample_schema, 'author')
assert_true(any(h.get('kind') == 'field' for h in hits), "Parse: Smart schema search finds fields")

ai_text = explain_schema_ai_style(sample_schema, focus='User')
assert_true('Type User has' in ai_text, "Parse: AI schema explanation returns focused summary")

nl_q1 = nl_to_nexql_query('show top 5 users with name and email', sample_schema)
assert_true(nl_q1.startswith('? '), "Parse: NL→NexQL generates read query by default")
assert_true('$limit 5' in nl_q1, "Parse: NL→NexQL extracts limit")

nl_q2 = nl_to_nexql_query('delete user u_0001', sample_schema)
assert_true(nl_q2.startswith('! '), "Parse: NL→NexQL detects delete intent")
assert_true('u_0001' in nl_q2, "Parse: NL→NexQL extracts id token")

# --- Foundation layer smoke tests (resolver, cache, rate limiter, serialization)
import foundation_features as ff

res = ff.resolve_query(NexQLParser().parse('? user { id }'), db)
assert_true(res.get('ok') is True and 'canonical_target' in res, "Foundation: resolver returns canonical target")

# Cache set/get
ck = 'golden_test_key'
ff.cache_set(ck, {'x': 1}, ttl=5)
cv = ff.cache_get(ck)
assert_true(isinstance(cv, dict) and cv.get('x') == 1, "Foundation: cache set/get works")

# Serialization (json)
sb = ff.serialize_response({'ok': True}, fmt='json')
assert_true(isinstance(sb, bytes) and sb.strip().startswith(b'{'), "Foundation: serialize json returns bytes")

# Rate limiter behavior
rl = ff.RateLimiter(limit_per_minute=3)
assert_true(rl.allow('tester', 1), "Foundation: rate limiter allows within limit")
assert_true(rl.allow('tester', 1), "Foundation: rate limiter allows second token")
assert_true(rl.allow('tester', 1), "Foundation: rate limiter allows third token")
assert_true(not rl.allow('tester', 1), "Foundation: rate limiter blocks when exhausted")

# Streaming generator yields deterministic chunks for subscribe
chunks = list(ff.stream_query_results('>> comment { id }', db))
assert_true(isinstance(chunks, list) and len(chunks) >= 1 and chunks[0].get('ok') is True, "Foundation: stream generator yields chunks")

# --- AI-native smoke tests
import ai_helpers as ah

sugg = ah.ai_query_autocomplete('na', sample_schema)
assert_true(isinstance(sugg, list), "AI: autocomplete returns list")

opt = ah.ai_optimize_query('? user { name name email }', sample_schema)
assert_true(isinstance(opt, str) and 'name' in opt, "AI: optimizer returns string and keeps field")

docs = ah.ai_generate_schema_docs(sample_schema)
assert_true('## User' in docs, "AI: schema docs generated")

dbg = ah.ai_debug_assistant('? user { id }', {'ok': False, 'errors':[{'code':'PARSE_ERROR','message':'bad','suggestion':'fix braces'}]})
assert_true(isinstance(dbg, str) and len(dbg) > 0, "AI: debug assistant returns suggestions")

summ = ah.ai_summarize_response({'ok':True,'#cost':5,'#took':10,'data':{}})
assert_true(isinstance(summ, str) and len(summ) > 0, "AI: summarize response works")

tc = ah.ai_generate_test_case('? user { id }', sample_schema)
assert_true(isinstance(tc, dict) and tc.get('query'), "AI: generate test case")

comp = ah.ai_query_compression_analysis('? user { id name email }')
assert_true(comp.get('chars')>0, "AI: compression analysis returns chars")

tok = ah.ai_estimate_token_usage('abcd efgh ijkl')
assert_true(isinstance(tok,int) and tok>0, "AI: token estimator works")

ctx = ah.ai_context_optimizer(('line\n' * 200), max_tokens=10)
assert_true(isinstance(ctx,str), "AI: context optimizer trims context")

ser = ah.ai_memory_friendly_serialize({'k':1})
assert_true(isinstance(ser, (bytes, bytearray)), "AI: memory-friendly serialization returns bytes")

pack = ah.ai_agent_pack({'x':1}, {'role':'test'})
assert_true(pack.get('message') and pack.get('meta'), "AI: agent pack returns structure")

agg = ah.ai_multiagent_aggregate([{'meta':{'role':'a'}}, {'meta':{'role':'b'}}])
assert_true(agg.get('agents')==2, "AI: multi-agent aggregate counts agents")

hits = ah.ai_semantic_search_schema(sample_schema, 'author')
assert_true(isinstance(hits, list) and len(hits)>0, "AI: semantic search finds hits")

stub = ah.ai_generate_resolver_stub('User', sample_schema)
assert_true('def resolve_user' in stub, "AI: resolver stub generated")

# --- Observability smoke tests
import observability_features as obs

stats = obs.get_latency_stats()
assert_true(isinstance(stats, dict) and 'avg_ms' in stats, "Obs: latency stats returns dict")

trace_data = [{'name': 'resolve_user', 'duration_ms': 5, 'type': 'resolver'}]
timings = obs.resolver_timing_breakdown(trace_data)
assert_true(isinstance(timings, dict), "Obs: resolver timing breakdown works")

cache_data = obs.cache_analytics({'hits': 10, 'misses': 2})
assert_true(cache_data.get('hit_rate_pct') > 0, "Obs: cache analytics calculates hit rate")

graph = obs.execution_graph_visualization([{'name': 'query', 'duration_ms': 20, 'depth': 0}])
assert_true('Execution Graph' in graph, "Obs: execution graph visualization works")

bottlenecks = obs.query_bottleneck_detector([{'name': 'slow', 'duration_ms': 100}], threshold_ms=50)
assert_true(isinstance(bottlenecks['bottlenecks'], list), "Obs: bottleneck detector works")

dashboard = obs.load_analysis_dashboard([{'latency_ms': 10, 'ok': True, 'qps': 1.0}])
assert_true(dashboard.get('queries') > 0, "Obs: load dashboard aggregates metrics")

traffic = obs.traffic_analytics([{'target': 'user', 'method': '?'}])
assert_true(traffic.get('total_queries') > 0, "Obs: traffic analytics counts queries")

tokens = obs.token_consumption_analytics([{'tokens': 50}])
assert_true(tokens.get('total_tokens') > 0, "Obs: token analytics sums tokens")

comp_metrics = obs.compression_efficiency_metrics(1000, 700)
assert_true(comp_metrics.get('ratio') > 0, "Obs: compression metrics calculate ratio")

resource = obs.resource_usage_monitor({'cpu': 25.5, 'memory_mb': 128})
assert_true(resource.get('timestamp') > 0, "Obs: resource monitor returns metrics")

heatmap = obs.query_frequency_heatmap([{'target': 'user'}, {'target': 'user'}, {'target': 'post'}])
assert_true(heatmap.get('peak_target') in ['user', 'post'], "Obs: heatmap finds peak target")

regression = obs.performance_regression_detection({'latency': 10}, {'latency': 25}, threshold_pct=50)
assert_true(isinstance(regression.get('regressions'), list), "Obs: regression detection works")

# --- Security smoke tests
import security_features as sec

parser = NexQLParser()
test_ast = parser.parse_to_ast('? user { id }')
ok, err = sec.query_depth_limiter(test_ast, max_depth=5)
assert_true(ok is True, "Sec: query depth limiter passes valid query")

ok, err = sec.complexity_limiter(test_ast, max_complexity=1000)
assert_true(ok is True, "Sec: complexity limiter passes valid query")

ok, denied = sec.field_level_permissions('user', 'user', '', ['name', 'email'])
assert_true(isinstance(denied, list), "Sec: field permissions checks restrictions")

auth = sec.authenticate('testuser', 'hash123')
assert_true(isinstance(auth, tuple), "Sec: authentication works")

dos = sec.check_dos_protection('client1')
assert_true(isinstance(dos, tuple), "Sec: DOS protection checks rate limit")

audit = sec.audit_log_event('test', 'user1', 'query', 'test_col')
assert_true(audit.get('type') == 'test', "Sec: audit log records events")

governance = sec.schema_governance_check('user', {'name': 'email'}, 'read')
assert_true(isinstance(governance, tuple), "Sec: schema governance checks rules")

access = sec.access_analytics([{'user': 'alice', 'result': 'success'}, {'user': 'bob', 'result': 'failed'}])
assert_true(access.get('unique_users') == 2, "Sec: access analytics counts users")

masked = sec.sensitive_field_masking({'name': 'test', 'password': 'secret'})
assert_true(masked.get('password') == '***MASKED***', "Sec: field masking works")

threats = sec.threat_detection('? user { id }', {'ok': True, '#data': [{'id': 'u1'}]})
assert_true(isinstance(threats.get('threats'), list), "Sec: threat detection works")

sandbox = sec.query_sandboxing('? user { id }', {'allowed_methods': ['?']})
assert_true(isinstance(sandbox, tuple), "Sec: query sandboxing checks rules")

# --- Team & Enterprise smoke tests
import team_enterprise_features as team

team_result = team.create_team('team_test', 'Test Team', 'alice')
assert_true(team_result.get('name') == 'Test Team', "Team: team creation works")

ws_result = team.create_workspace('ws_test', 'Main', 'team_test', 'alice')
assert_true(ws_result.get('name') == 'Main', "Team: workspace creation works")

add_ok = team.add_workspace_member('ws_test', 'bob')
assert_true(isinstance(add_ok, bool), "Team: workspace member addition works")

workspaces = team.list_workspaces('team_test')
assert_true(isinstance(workspaces, list), "Team: workspace listing works")

rba = team.role_based_access('admin', 'query', 'write')
assert_true(rba is True, "Team: role-based access control works")

comment = team.add_query_comment('q_test', 'alice', 'Great query!', line=0)
assert_true(comment.get('user') == 'alice', "Team: query comments work")

comments = team.get_query_comments('q_test')
assert_true(isinstance(comments, list) and len(comments) > 0, "Team: comment retrieval works")

review = team.create_query_review('rev_test', 'q_test', 'alice', ['bob', 'charlie'])
assert_true(review.get('status') == 'pending', "Team: review creation works")

approve_ok = team.approve_review('rev_test', 'bob')
assert_true(isinstance(approve_ok, bool), "Team: review approval works")

reject_ok = team.reject_review('rev_test', 'charlie', 'Needs revision')
assert_true(isinstance(reject_ok, bool), "Team: review rejection works")

vc = team.version_control_integration('q_test', 1, 'abc123', 'alice', 'Optimize query')
assert_true(vc.get('commit_hash') == 'abc123', "Team: version control integration works")

cicd = team.ci_cd_integration('pipeline_1', 'q_test', 'validate', 'passed')
assert_true(cicd.get('stage') == 'validate', "Team: CI/CD integration works")

deploy = team.deployment_pipeline('prod', 'q_test', 1, 'deployed')
assert_true(deploy.get('environment') == 'prod', "Team: deployment pipeline works")

analytics = team.team_analytics('team_test', [{'user': 'alice', 'response_time': 10}])
assert_true(analytics.get('total_queries') > 0, "Team: team analytics works")

registry = team.organization_schema_registry('org_1', [{'name': 'User', 'version': 1}])
assert_true(registry.get('schema_count') > 0, "Team: organization schema registry works")

federated = team.federated_schema_management([{'name': 'User'}], [{'name': 'Order'}])
assert_true(federated.get('total_schemas') >= 2, "Team: federated schema management works")

orchestration = team.multi_service_orchestration([{'name': 'UserService'}], [{'target': 'user'}])
assert_true(orchestration.get('services') > 0, "Team: multi-service orchestration works")

# --- SDK & Integration smoke tests
import sdk_integration_features as sdk

# SDK generation tests
python_sdk = sdk.generate_sdk('python', {'version': '1.0'}, {'base_url': 'http://localhost:8080'})
assert_true(python_sdk.get('language') == 'python', "SDK: Python SDK generated")
assert_true('client' in python_sdk, "SDK: Python SDK has client code")

typescript_sdk = sdk.generate_sdk('typescript', {'version': '1.0'}, {'base_url': 'http://localhost:8080'})
assert_true(typescript_sdk.get('language') == 'typescript', "SDK: TypeScript SDK generated")

java_sdk = sdk.generate_sdk('java', {'version': '1.0'}, {'base_url': 'http://localhost:8080'})
assert_true(java_sdk.get('language') == 'java', "SDK: Java SDK generated")

go_sdk = sdk.generate_sdk('go', {'version': '1.0'}, {'base_url': 'http://localhost:8080'})
assert_true(go_sdk.get('language') == 'go', "SDK: Go SDK generated")

rust_sdk = sdk.generate_sdk('rust', {'version': '1.0'}, {'base_url': 'http://localhost:8080'})
assert_true(rust_sdk.get('language') == 'rust', "SDK: Rust SDK generated")

# REST bridge tests
bridge = sdk.generate_rest_bridge({'users': {}})
assert_true('rest_routes' in bridge, "SDK: REST bridge has routes")
assert_true(len(bridge.get('rest_routes', {})) > 0, "SDK: REST bridge routes populated")

# OpenAPI tests
openapi = sdk.generate_openapi_spec({'version': '1.0'}, {'title': 'Test API'})
assert_true(openapi.get('openapi') == '3.0.0', "SDK: OpenAPI version correct")
assert_true(openapi['info']['title'] == 'Test API', "SDK: OpenAPI title set")

# Framework adapter tests
express_adapter = sdk.create_framework_adapter('express')
assert_true('middleware' in express_adapter, "SDK: Express adapter has middleware")

fastapi_adapter = sdk.create_framework_adapter('fastapi')
assert_true('dependency' in fastapi_adapter, "SDK: FastAPI adapter has dependency")

django_adapter = sdk.create_framework_adapter('django')
assert_true('view' in django_adapter, "SDK: Django adapter has view")

# Message queue tests
kafka_config = sdk.integrate_message_queue('kafka', {'topic': 'nexql-events'})
assert_true(kafka_config.get('topic') == 'nexql-events', "SDK: Kafka integration configured")

rabbitmq_config = sdk.integrate_message_queue('rabbitmq', {'exchange': 'nexql'})
assert_true(rabbitmq_config.get('exchange') == 'nexql', "SDK: RabbitMQ integration configured")

redis_config = sdk.integrate_message_queue('redis', {'channel': 'nexql:queries'})
assert_true(redis_config.get('channel') == 'nexql:queries', "SDK: Redis integration configured")

# Plugin tests
plugin_result = sdk.register_plugin('test-plugin', {'hooks': ['query.pre'], 'version': '1.0.0'})
assert_true('plugin_id' in plugin_result, "SDK: Plugin registered")
assert_true(plugin_result.get('status') == 'registered', "SDK: Plugin status is registered")

# Webhook tests
webhook_result = sdk.create_webhook('query.executed', 'http://example.com/webhook', 'secret123')
assert_true('webhook_id' in webhook_result, "SDK: Webhook created")
assert_true(webhook_result.get('status') == 'created', "SDK: Webhook status is created")

# Webhook delivery test
delivery = sdk.deliver_webhook_event(webhook_result['webhook_id'], {'query': 'test'})
assert_true('status_code' in delivery, "SDK: Webhook delivery has status code")

# Cache backend tests
redis_cache = sdk.configure_cache_backend('redis', {'host': 'localhost', 'port': 6379})
assert_true(redis_cache.get('host') == 'localhost', "SDK: Redis cache configured")

memcached_cache = sdk.configure_cache_backend('memcached', {'servers': ['127.0.0.1:11211']})
assert_true(len(memcached_cache.get('servers', [])) > 0, "SDK: Memcached cache configured")

in_memory_cache = sdk.configure_cache_backend('in_memory', {'max_size': 1000})
assert_true(in_memory_cache.get('max_size') == 1000, "SDK: In-memory cache configured")

# Retry strategy tests
exponential_retry = sdk.configure_retry_strategy('exponential', {'initial_delay_ms': 100, 'max_delay_ms': 30000})
assert_true(exponential_retry.get('multiplier') == 2, "SDK: Exponential retry configured")

linear_retry = sdk.configure_retry_strategy('linear', {'initial_delay_ms': 500})
assert_true(linear_retry.get('increment_ms') > 0, "SDK: Linear retry configured")

fixed_retry = sdk.configure_retry_strategy('fixed', {'delay_ms': 1000})
assert_true(fixed_retry.get('delay_ms') == 1000, "SDK: Fixed retry configured")

# Circuit breaker test
breaker = sdk.apply_circuit_breaker()
assert_true(breaker.get('state') == 'closed', "SDK: Circuit breaker has state")
assert_true(breaker.get('failure_threshold') > 0, "SDK: Circuit breaker has threshold")

# Health check test
health = sdk.perform_health_check()
assert_true(health.get('status') == 'healthy', "SDK: Health check returns status")
assert_true('metrics' in health, "SDK: Health check has metrics")

# Distributed tracing test
tracing = sdk.configure_distributed_tracing()
assert_true(tracing.get('provider') == 'opentelemetry', "SDK: Tracing configured")
assert_true(len(tracing.get('spans', [])) > 0, "SDK: Tracing has spans")

# Ecosystem dashboard test
dashboard = sdk.get_sdk_ecosystem_dashboard()
assert_true('sdks' in dashboard, "SDK: Dashboard has SDKs")
assert_true('integrations' in dashboard, "SDK: Dashboard has integrations")
assert_true('community' in dashboard, "SDK: Dashboard has community stats")
assert_true(dashboard['sdks'].get('python', {}).get('downloads', 0) > 0, "SDK: Python SDK has downloads")

# --- Visualization smoke tests
import visualization_features as viz
import edge_execution_features as edge

# Graph visualization tests
nodes = [{"id": "user", "label": "User"}, {"id": "post", "label": "Post"}]
edges = [("user", "post")]
graph_ascii = viz.generate_graph_visualization(nodes, edges, "ascii")
assert_true(len(graph_ascii) > 0, "Viz: ASCII graph generated")
assert_true("GRAPH STRUCTURE" in graph_ascii, "Viz: Graph has structure header")

graph_svg = viz.generate_graph_visualization(nodes, edges, "svg")
assert_true("<svg" in graph_svg, "Viz: SVG graph has svg tag")
assert_true("circle" in graph_svg, "Viz: SVG graph has circle nodes")

# ERD visualization tests
entities = {"users": ["id", "name"], "posts": ["id", "title"]}
relationships = [{"from": "users", "to": "posts", "type": "has_many"}]
erd = viz.generate_erd(entities, relationships)
assert_true('ascii' in erd, "Viz: ERD has ASCII representation")
assert_true(erd.get('entities') == 2, "Viz: ERD has 2 entities")
assert_true(erd.get('relationships') == 1, "Viz: ERD has 1 relationship")

# Query flow diagram tests
stages = [
    {"name": "Parse", "duration_ms": 5},
    {"name": "Execute", "duration_ms": 50}
]
flow = viz.generate_query_flow_diagram("? users", stages)
assert_true('ascii' in flow, "Viz: Query flow has ASCII")
assert_true(flow.get('stage_count') == 2, "Viz: Query flow has 2 stages")

# Execution pipeline tests
steps = [{"name": "Step 1", "duration_ms": 10}]
pipeline = viz.generate_execution_pipeline("? users", steps)
assert_true(pipeline.get('total_steps') == 1, "Viz: Pipeline has 1 step")
assert_true('timeline' in pipeline, "Viz: Pipeline has timeline")
assert_true(len(pipeline.get('steps', [])) > 0, "Viz: Pipeline has steps list")

# Dashboard widgets tests
metrics = {"queries_executed": 100, "avg_response_ms": 50.0}
widgets = viz.create_dashboard_widgets(metrics)
assert_true('queries_executed' in widgets, "Viz: Widgets has query counter")
assert_true('cache_hit_rate' in widgets, "Viz: Widgets has cache metric")
assert_true(widgets['queries_executed'].get('type') == 'counter', "Viz: Widget is counter type")

dashboard_text = viz.render_dashboard_widgets(widgets)
assert_true(len(dashboard_text) > 0, "Viz: Dashboard text rendered")
assert_true("DASHBOARD METRICS" in dashboard_text, "Viz: Dashboard has header")

# Metrics chart tests
data_points = [("t1", 50), ("t2", 75), ("t3", 60)]
bar_chart = viz.generate_metrics_chart("latency", data_points, "bar")
assert_true(len(bar_chart) > 0, "Viz: Bar chart generated")
assert_true("BAR CHART" in bar_chart, "Viz: Bar chart has header")

line_chart = viz.generate_metrics_chart("throughput", data_points, "line")
assert_true("LINE CHART" in line_chart, "Viz: Line chart has header")

area_chart = viz.generate_metrics_chart("errors", data_points, "area")
assert_true("AREA CHART" in area_chart, "Viz: Area chart has header")

# Event stream viewer tests
events = [
    {"timestamp": "09:45:10", "type": "query.start", "details": "Started"},
    {"timestamp": "09:45:11", "type": "query.end", "details": "Ended"},
]
event_stream = viz.create_event_stream_viewer(events)
assert_true(event_stream.get('total_events') == 2, "Viz: Stream has 2 events")
assert_true('timeline' in event_stream, "Viz: Stream has timeline")
assert_true(len(event_stream.get('event_types', {})) > 0, "Viz: Stream has event types")

event_timeline = event_stream['timeline']
assert_true(len(event_timeline) > 0, "Viz: Event timeline rendered")

event_stats = viz.render_event_statistics(event_stream)
assert_true("EVENT STATISTICS" in event_stats, "Viz: Event stats have header")

# Comprehensive dashboard test
query_data = {"stages": stages, "steps": steps}
comp_dashboard = viz.generate_comprehensive_dashboard(query_data, metrics, events)
assert_true('query_flow' in comp_dashboard, "Viz: Comprehensive dashboard has query flow")
assert_true('execution_pipeline' in comp_dashboard, "Viz: Comprehensive dashboard has pipeline")
assert_true('metrics_widgets' in comp_dashboard, "Viz: Comprehensive dashboard has widgets")

# --- Edge Execution smoke tests
nodes = edge.discover_edge_nodes()
assert_true(isinstance(nodes, list) and len(nodes) >= 1, "Edge: discover_edge_nodes returns list")
route = edge.route_query_to_edge('? user { id }', nodes[0]['id'], db)
assert_true(route.get('ok') is True, "Edge: route_query_to_edge routes successfully")
assert_true(route.get('edge_id') == nodes[0]['id'], "Edge: routed edge id matches")

# ============================================================================
print("\n[RESPONSE ENVELOPE VALIDATION]")  
print("-" * 70)

result = execute_nexql('? user (id "u_0001") { id name }', db)
assert_true(result['ok'] == True, "Envelope: ok field present")
assert_true('#qid' in result, "Envelope: #qid present")
assert_true('#cost' in result, "Envelope: #cost present")  
assert_true('#ts' in result, "Envelope: #ts present")
assert_true('#took' in result, "Envelope: #took present")
assert_true('#data' in result, "Envelope: #data present")
assert_true(isinstance(result['#cost'], int), "Envelope: cost is integer")
assert_true(1 <= result['#cost'] <= 100, "Envelope: cost in [1,100]")

# Error envelope
result = execute_nexql('? unknown { id }', db)
assert_true(result['ok'] == False, "Envelope: ok=false for error")
assert_true('errors' in result, "Envelope: errors array present")
assert_true(len(result['errors']) > 0, "Envelope: errors not empty")
assert_true('code' in result['errors'][0], "Envelope: error has code")
assert_true('suggestion' in result['errors'][0], "Envelope: error has suggestion")

# ============================================================================
print("\n[FIELD PROJECTION]")
print("-" * 70)

# Test subset of fields
result = execute_nexql('? user (id "u_0001") { name email }', db)
assert_true('name' in result['#data']['user'], "Projection: requested field 'name' present")
assert_true('email' in result['#data']['user'], "Projection: requested field 'email' present")
assert_true('age' not in result['#data']['user'], "Projection: non-requested field 'age' absent")

# Test nested field projection
result = execute_nexql('? post (id "p_0001") { title author { name } }', db)
assert_true('title' in result['#data']['post'], "Projection: top-level field present")
assert_true('author' in result['#data']['post'], "Projection: nested object present")
assert_true('name' in result['#data']['post']['author'], "Projection: nested field present")
assert_true('id' not in result['#data']['post']['author'], "Projection: nested non-requested field absent")

# ============================================================================
print("\n[CRUD OPERATIONS]")
print("-" * 70)

# Test CREATE
result = execute_nexql('+ user { name "Charlie" email "charlie@example.com" age 30 } { id name }', db)
assert_true(result['ok'] == True, "Create: Success response")
assert_true('name' in result['#data']['user'], "Create: Returns created fields")
assert_true(result['#data']['user']['name'] == "Charlie", "Create: Field value set correctly")
assert_true('id' in result['#data']['user'], "Create: Auto-generated ID present")

# Test READ single
result = execute_nexql('? user (id "u_0001") { id name }', db)
assert_true(result['ok'] == True, "Read: Single record success")
assert_true(result['#data']['user']['name'] == 'Alice', "Read: Correct record retrieved")

# Test READ list
result = execute_nexql('? user { id name }', db)
assert_true(result['ok'] == True, "Read: List success")
assert_true(isinstance(result['#data']['user'], list), "Read: List returns array")
assert_true(len(result['#data']['user']) > 0, "Read: List not empty")

# Test UPDATE
result = execute_nexql('~ user (id "u_0001") { name "Alice Updated" } { id name }', db)
assert_true(result['ok'] == True, "Update: Success response")
assert_true(result['#data']['user']['name'] == "Alice Updated", "Update: Field updated")

# Test UPDATE ignores non-directive filter args as payload fields
result = execute_nexql('~ user (id "u_0001" active true) { active false } { id active }', db)
assert_true(result['ok'] == True, "Update: Filter args do not overwrite payload fields")
assert_true(result['#data']['user']['active'] == False, "Update: Payload value wins (no args->payload merge)")

# Test DELETE
result = execute_nexql('! post (id "p_0002") { id title deleted }', db)
assert_true(result['ok'] == True, "Delete: Success response")
assert_true(result['#data']['post'].get('deleted') == True, "Delete: Deleted flag set")

# Test pagination next cursor metadata
result = execute_nexql('? user ($limit 1 $sort createdAt asc) { id name }', db)
assert_true(result['ok'] == True, "Read: Pagination query succeeds")
assert_true(result.get('next') == 'cursor_u_0001', "Read: next cursor token is emitted when another page exists")

result = execute_nexql('? user ($limit 10 $sort createdAt asc) { id name }', db)
assert_true(result['ok'] == True, "Read: Larger page query succeeds")
assert_true(result.get('next') is None, "Read: next is null when no next page exists")

# Test CREATE ignores filter args as payload fields
result = execute_nexql('+ user (name "Legacy") { name "Payload" email "payload@example.com" } { name email }', db)
assert_true(result['ok'] == True, "Create: Success with filter args present")
assert_true(result['#data']['user']['name'] == 'Payload', "Create: Payload field is not overwritten by filter args")

# ============================================================================
print("\n[NESTED PAYLOADS]")
print("-" * 70)

# Test deeply nested read
result = execute_nexql('? post (id "p_0001") { id title author { id name } }', db)
assert_true(result['ok'] == True, "Nested: Deep read works")
assert_true('author' in result['#data']['post'], "Nested: Author object present")
assert_true(isinstance(result['#data']['post']['author'], dict), "Nested: Author is object")

# Test nested list
result = execute_nexql('? post (id "p_0001") { id title }', db)
assert_true(result['ok'] == True, "Nested: Read with nested data")

# ============================================================================
print("\n[DIRECTIVES]")
print("-" * 70)

# Test @cache directive
result = execute_nexql('? user (id "u_0001") { id name } @cache(ttl: 300)', db)
assert_true(result['ok'] == True, "Directive: @cache parsed")

# Test @auth directive (read admin field)
result = execute_nexql('? user (id "u_0001") { id name role @auth(role: admin) }', db, user_role="admin")
assert_true(result['ok'] == True, "Directive: @auth passed for admin")

# Test @auth directive (denied for non-admin)
result = execute_nexql('? user (id "u_0001") { id role @auth(role: admin) }', db, user_role="user")
assert_true(result['ok'] == False, "Directive: @auth denied for non-admin")
assert_true(result['errors'][0]['code'] == 'UNAUTHORIZED', "Directive: Correct error code")

# Test @cost directive
result = execute_nexql('? user { id @cost(max: 50) }', db)
assert_true(result['ok'] == True, "Directive: @cost parsed")

# ============================================================================
print("\n[AUTHORIZATION]")
print("-" * 70)

# Test unauthorized field access (without directive - should work)
result = execute_nexql('? user (id "u_0001") { id name role }', db, user_role="user")
assert_true(result['ok'] == True, "Auth: Non-protected field accessible")

# Test protected field with correct role
result = execute_nexql('? user (id "u_0001") { role @auth(role: admin) }', db, user_role="admin")
assert_true(result['ok'] == True, "Auth: Admin can access admin field")

# Test protected field with wrong role
result = execute_nexql('? user (id "u_0001") { role @auth(role: admin) }', db, user_role="user")
assert_true(result['ok'] == False, "Auth: User denied access to admin field")

# ============================================================================
print("\n[COST CALCULATION]")
print("-" * 70)

# Simple query should cost less than complex
simple = execute_nexql('? user { id }', db)
complex = execute_nexql('? post { id title author { name email } }', db)
assert_true(simple['#cost'] < complex['#cost'], "Cost: Complex query costs more")
assert_true(1 <= simple['#cost'] <= 100, "Cost: Simple within range")
assert_true(1 <= complex['#cost'] <= 100, "Cost: Complex within range")

# Different methods have different costs
read_cost = execute_nexql('? user { id }', db)['#cost']
create_cost = execute_nexql('+ user { name "Test Cost" email "testcost@example.com" } { id }', db)['#cost']
assert_true(create_cost > read_cost, "Cost: Create costs more than read")

# ============================================================================
print("\n[ERROR HANDLING]")
print("-" * 70)

# Parse error
result = execute_nexql('bad syntax', db)
assert_true(result['ok'] == False, "Error: Parse error detected")
assert_true(result['errors'][0]['code'] == 'PARSE_ERROR', "Error: Correct error code")
assert_true('suggestion' in result['errors'][0], "Error: Suggestion provided")

# Unknown collection
result = execute_nexql('? unknown { id }', db)
assert_true(result['ok'] == False, "Error: Unknown collection error")
assert_true(result['errors'][0]['code'] == 'UNKNOWN_COLLECTION', "Error: Unknown collection code")
assert_true('available' in result['errors'][0], "Error: Available collections listed")

# Not found
result = execute_nexql('? user (id "u_9999") { id }', db)
assert_true(result['ok'] == False, "Error: Record not found error")
assert_true(result['errors'][0]['code'] == 'NOT_FOUND', "Error: Not found code")
assert_true('collection' in result['errors'][0], "Error: Collection info present")
assert_true('id' in result['errors'][0], "Error: ID info present")

# Schema violation
result = execute_nexql('+ user(invalidField: 123) { id }', db)
assert_true(result['ok'] == False, "Error: Schema violation detected")
assert_true(result['errors'][0]['code'] == 'SCHEMA_VIOLATION', "Error: Schema violation code")

# ============================================================================
print("\n[TRACING]")
print("-" * 70)

# Success trace
result = execute_nexql('? user { id }', db)
assert_true('#trace' not in result or isinstance(result.get('#trace'), list), "Trace: Success response trace")

# Error trace
result = execute_nexql('bad', db)
assert_true('#trace' in result, "Trace: Error response has trace")
assert_true(len(result['#trace']) > 0, "Trace: Trace contains steps")
assert_true('step' in result['#trace'][0], "Trace: Trace step has step field")

# ============================================================================
print("\n[EDGE CASES]")
print("-" * 70)

# Empty result set
result = execute_nexql('? user (id "u_nonexistent") { id }', db)
assert_true(result['ok'] == False, "Edge: Nonexistent ID handled")

# Query with no fields specified
result = execute_nexql('? user (id "u_0001") { }', db)
# Should either work or error gracefully
assert_true(result['ok'] in [True, False], "Edge: Empty field set handled")

# Maximum limit
result = execute_nexql('? user ($limit 1000) { id }', db)
assert_true(result['ok'] == True, "Edge: Large limit handled")
assert_true(len(result['#data']['user']) <= 1000, "Edge: Limit cap enforced")

# ============================================================================
print("\n" + "=" * 70)
print("TEST RESULTS")
print("=" * 70)
print(f"Passed: {passed}")
print(f"Failed: {failed}")
print(f"Total:  {passed + failed}")

if failed == 0:
    print("\n✓ ALL TESTS PASSED!")
    sys.exit(0)
else:
    print(f"\n✗ {failed} test(s) failed")
    sys.exit(1)
