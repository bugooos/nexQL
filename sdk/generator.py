"""
nexql/sdk/generator.py
──────────────────────
Multi-language SDK generator.

WHY SEPARATE FROM sdk_integration_features.py:
  sdk_integration_features.py mixed SDK generation with:
    • webhook management (→ transport layer concern)
    • plugin architecture (→ plugins/loader.py)
    • Kafka/RabbitMQ integration stubs (→ transport layer)
    • metrics export (→ observability)
    • health check system (→ transport)

  This module ONLY generates SDK client code from a schema.

PUBLIC API:
  SDKGenerator(schema_list).generate(language, config) -> SDKOutput
  Supported languages: python, javascript, typescript, java, go, rust
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SDKOutput:
    language:    str
    client_code: str
    config:      dict
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    files:       dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "language":     self.language,
            "client":       self.client_code,
            "config":       self.config,
            "generated_at": self.generated_at,
            "files":        self.files,
        }


class SDKGenerator:
    """Generate typed SDK clients from a NexQL schema."""

    def __init__(self, schema: list[dict]) -> None:
        self._schema = schema or []

    def generate(self, language: str, config: Optional[dict] = None) -> SDKOutput:
        config = config or {}
        base_url  = config.get("base_url", "http://localhost:7433")
        auth_type = config.get("auth_type", "api_key")
        version   = config.get("schema_version", "1.0.0")

        sdk_config = {
            "language":       language,
            "base_url":       base_url,
            "auth_type":      auth_type,
            "schema_version": version,
            "timestamp":      datetime.now().isoformat(),
        }

        generators = {
            "python":     self._python,
            "javascript": self._javascript,
            "typescript": self._typescript,
            "java":       self._java,
            "go":         self._go,
            "rust":       self._rust,
        }

        fn = generators.get(language.lower())
        if not fn:
            return SDKOutput(language=language, client_code=f"# Language '{language}' not supported",
                             config=sdk_config)

        code = fn(sdk_config)
        return SDKOutput(language=language, client_code=code, config=sdk_config)

    # ── Python ─────────────────────────────────────────────────────────────

    def _python(self, cfg: dict) -> str:
        types = "\n".join(
            f"    {t['name'].lower()}s = NexQLCollection('{t['name'].lower()}s')"
            for t in self._schema
        )
        return f"""# NexQL Python SDK — Auto-generated {cfg['timestamp']}
# Schema version: {cfg['schema_version']}
import requests
from dataclasses import dataclass
from typing import Any, Optional

BASE_URL = "{cfg['base_url']}"

class NexQLClient:
    def __init__(self, api_key: str = "", base_url: str = BASE_URL):
        self.base_url = base_url
        self.headers  = {{"Authorization": f"Bearer {{api_key}}", "Content-Type": "application/json"}}

    def execute(self, query: str, variables: dict = None, role: str = "user") -> dict:
        payload = {{"query": query, "variables": variables or {{}}, "user_role": role}}
        resp = requests.post(f"{{self.base_url}}/execute", json=payload, headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def read(self, target: str, filters: dict = None, fields: list = None, limit: int = 20) -> dict:
        field_str = " ".join(fields or ["id", "createdAt"])
        filter_str = " ".join(f'{{k}} "{{v}}"' for k, v in (filters or {{}}).items())
        query = f"? {{target}} ({{filter_str}} $limit {{limit}}) {{ {{field_str}} }}"
        return self.execute(query)

    def create(self, target: str, payload: dict, return_fields: list = None) -> dict:
        field_str = " ".join(return_fields or ["id", "createdAt"])
        kv = " ".join(f'{{k}} "{{v}}"' for k, v in payload.items())
        query = f"+ {{target}} {{ {{kv}} }} {{ {{field_str}} }}"
        return self.execute(query)

    def update(self, target: str, id: str, updates: dict, return_fields: list = None) -> dict:
        field_str = " ".join(return_fields or ["id", "updatedAt"])
        kv = " ".join(f'{{k}} "{{v}}"' for k, v in updates.items())
        query = f'~ {{target}} (id "{{id}}") {{ {{kv}} }} {{ {{field_str}} }}'
        return self.execute(query)

    def delete(self, target: str, id: str) -> dict:
        query = f'! {{target}} (id "{{id}}") {{ id }}'
        return self.execute(query)

client = NexQLClient()
"""

    # ── JavaScript ─────────────────────────────────────────────────────────

    def _javascript(self, cfg: dict) -> str:
        return f"""// NexQL JavaScript SDK — Auto-generated {cfg['timestamp']}
// Schema version: {cfg['schema_version']}
const BASE_URL = '{cfg['base_url']}';

class NexQLClient {{
  constructor(apiKey = '', baseUrl = BASE_URL) {{
    this.baseUrl = baseUrl;
    this.headers = {{ 'Authorization': `Bearer ${{apiKey}}`, 'Content-Type': 'application/json' }};
  }}

  async execute(query, variables = {{}}, role = 'user') {{
    const res = await fetch(`${{this.baseUrl}}/execute`, {{
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify({{ query, variables, user_role: role }}),
    }});
    if (!res.ok) throw new Error(`NexQL request failed: ${{res.status}}`);
    return res.json();
  }}

  read(target, filters = {{}}, fields = ['id'], limit = 20) {{
    const fieldStr  = fields.join(' ');
    const filterStr = Object.entries(filters).map(([k,v]) => `${{k}} "${{v}}"`).join(' ');
    return this.execute(`? ${{target}} (${{filterStr}} $limit ${{limit}}) {{ ${{fieldStr}} }}`);
  }}

  create(target, payload, returnFields = ['id', 'createdAt']) {{
    const fieldStr = returnFields.join(' ');
    const kv       = Object.entries(payload).map(([k,v]) => `${{k}} "${{v}}"`).join(' ');
    return this.execute(`+ ${{target}} {{ ${{kv}} }} {{ ${{fieldStr}} }}`);
  }}

  update(target, id, updates, returnFields = ['id', 'updatedAt']) {{
    const fieldStr = returnFields.join(' ');
    const kv       = Object.entries(updates).map(([k,v]) => `${{k}} "${{v}}"`).join(' ');
    return this.execute(`~ ${{target}} (id "${{id}}") {{ ${{kv}} }} {{ ${{fieldStr}} }}`);
  }}

  delete(target, id) {{
    return this.execute(`! ${{target}} (id "${{id}}") {{ id }}`);
  }}
}}

module.exports = {{ NexQLClient }};
"""

    # ── TypeScript ─────────────────────────────────────────────────────────

    def _typescript(self, cfg: dict) -> str:
        return f"""// NexQL TypeScript SDK — Auto-generated {cfg['timestamp']}
export interface NexQLResponse {{ ok: boolean; [key: string]: any; }}

export class NexQLClient {{
  constructor(
    private readonly apiKey: string = '',
    private readonly baseUrl: string = '{cfg['base_url']}'
  ) {{}}

  async execute(query: string, variables: Record<string, any> = {{}}, role = 'user'): Promise<NexQLResponse> {{
    const res = await fetch(`${{this.baseUrl}}/execute`, {{
      method: 'POST',
      headers: {{ Authorization: `Bearer ${{this.apiKey}}`, 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ query, variables, user_role: role }}),
    }});
    return res.json();
  }}

  read(target: string, filters: Record<string, any> = {{}}, fields: string[] = ['id'], limit = 20) {{
    const filterStr = Object.entries(filters).map(([k, v]) => `${{k}} "${{v}}"`).join(' ');
    return this.execute(`? ${{target}} (${{filterStr}} $limit ${{limit}}) {{ ${{fields.join(' ')}} }}`);
  }}

  create(target: string, payload: Record<string, any>, returnFields = ['id', 'createdAt']) {{
    const kv = Object.entries(payload).map(([k, v]) => `${{k}} "${{v}}"`).join(' ');
    return this.execute(`+ ${{target}} {{ ${{kv}} }} {{ ${{returnFields.join(' ')}} }}`);
  }}
}}
"""

    # ── Java ───────────────────────────────────────────────────────────────

    def _java(self, cfg: dict) -> str:
        return f"""// NexQL Java SDK — Auto-generated {cfg['timestamp']}
import java.net.http.*;
import java.net.URI;
import java.util.Map;
import com.fasterxml.jackson.databind.ObjectMapper;

public class NexQLClient {{
    private final String baseUrl;
    private final String apiKey;
    private final HttpClient http = HttpClient.newHttpClient();
    private final ObjectMapper mapper = new ObjectMapper();

    public NexQLClient(String apiKey) {{
        this("{cfg['base_url']}", apiKey);
    }}
    public NexQLClient(String baseUrl, String apiKey) {{
        this.baseUrl = baseUrl;
        this.apiKey  = apiKey;
    }}

    public Map<String, Object> execute(String query) throws Exception {{
        String body = mapper.writeValueAsString(Map.of("query", query));
        HttpRequest req = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + "/execute"))
            .header("Content-Type", "application/json")
            .header("Authorization", "Bearer " + apiKey)
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build();
        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        return mapper.readValue(resp.body(), Map.class);
    }}
}}
"""

    # ── Go ─────────────────────────────────────────────────────────────────

    def _go(self, cfg: dict) -> str:
        return f"""// NexQL Go SDK — Auto-generated {cfg['timestamp']}
package nexql

import (
    "bytes"
    "encoding/json"
    "fmt"
    "net/http"
)

type Client struct {{
    BaseURL string
    APIKey  string
    http    *http.Client
}}

func NewClient(apiKey string) *Client {{
    return &Client{{BaseURL: "{cfg['base_url']}", APIKey: apiKey, http: &http.Client{{}}}}
}}

func (c *Client) Execute(query string, variables map[string]any) (map[string]any, error) {{
    body, _ := json.Marshal(map[string]any{{"query": query, "variables": variables}})
    req, _ := http.NewRequest("POST", c.BaseURL+"/execute", bytes.NewReader(body))
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", c.APIKey))
    resp, err := c.http.Do(req)
    if err != nil {{
        return nil, err
    }}
    defer resp.Body.Close()
    var result map[string]any
    json.NewDecoder(resp.Body).Decode(&result)
    return result, nil
}}
"""

    # ── Rust ───────────────────────────────────────────────────────────────

    def _rust(self, cfg: dict) -> str:
        return f"""// NexQL Rust SDK — Auto-generated {cfg['timestamp']}
use reqwest::Client;
use serde_json::{{json, Value}};

pub struct NexQLClient {{
    base_url: String,
    api_key:  String,
    client:   Client,
}}

impl NexQLClient {{
    pub fn new(api_key: &str) -> Self {{
        Self {{
            base_url: "{cfg['base_url']}".to_string(),
            api_key:  api_key.to_string(),
            client:   Client::new(),
        }}
    }}

    pub async fn execute(&self, query: &str) -> Result<Value, reqwest::Error> {{
        self.client
            .post(format!("{{base_url}}/execute", base_url=self.base_url))
            .bearer_auth(&self.api_key)
            .json(&json!({{"query": query}}))
            .send()
            .await?
            .json()
            .await
    }}
}}
"""
