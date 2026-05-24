# NOTE: This module has been moved from the root of the monolith into nexql/runtime/.
# It contains NO UI imports and is callable via the IPC bridge through server_entry.py.
# Future work: further decompose visualization into nexql/schema/ analysis helpers
# and a thin ide/ rendering layer.

"""Team collaboration and enterprise features for NexQL Workbench.
Provides team workspaces, role-based access, query commenting, review systems,
version control integration, CI/CD pipelines, deployment management, analytics,
and federated schema management.
"""
import json
import time
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
from pathlib import Path

# Team/org data files
TEAMS_FILE = Path.home() / ".piql-workbench" / "teams.json"
WORKSPACES_FILE = Path.home() / ".piql-workbench" / "workspaces.json"
QUERIES_FILE = Path.home() / ".piql-workbench" / "team_queries.json"

class TeamManager:
    """Manage team membership and roles."""
    def __init__(self):
        self.teams = {}
        self.members = defaultdict(list)
    
    def create_team(self, team_id: str, name: str, owner: str) -> dict:
        team = {
            'id': team_id,
            'name': name,
            'owner': owner,
            'created_at': int(time.time()),
            'members': [owner],
        }
        self.teams[team_id] = team
        self.members[team_id].append(owner)
        return team
    
    def add_member(self, team_id: str, user: str, role: str = 'member') -> bool:
        if team_id not in self.teams:
            return False
        if user not in self.members[team_id]:
            self.members[team_id].append(user)
            return True
        return False
    
    def list_members(self, team_id: str) -> list:
        return self.members.get(team_id, [])
    
    def get_team_info(self, team_id: str) -> Optional[dict]:
        return self.teams.get(team_id)

_team_manager = TeamManager()

def create_team(team_id: str, name: str, owner: str) -> dict:
    return _team_manager.create_team(team_id, name, owner)

def add_team_member(team_id: str, user: str, role: str = 'member') -> bool:
    return _team_manager.add_member(team_id, user, role)

def list_team_members(team_id: str) -> list:
    return _team_manager.list_members(team_id)

class WorkspaceManager:
    """Manage shared workspaces."""
    def __init__(self):
        self.workspaces = {}
    
    def create_workspace(self, ws_id: str, name: str, team_id: str, owner: str) -> dict:
        ws = {
            'id': ws_id,
            'name': name,
            'team_id': team_id,
            'owner': owner,
            'created_at': int(time.time()),
            'members': [owner],
            'queries': [],
        }
        self.workspaces[ws_id] = ws
        return ws
    
    def add_workspace_member(self, ws_id: str, user: str) -> bool:
        if ws_id not in self.workspaces:
            return False
        ws = self.workspaces[ws_id]
        if user not in ws['members']:
            ws['members'].append(user)
            return True
        return False
    
    def list_workspaces(self, team_id: str) -> list:
        return [ws for ws in self.workspaces.values() if ws['team_id'] == team_id]

_workspace_manager = WorkspaceManager()

def create_workspace(ws_id: str, name: str, team_id: str, owner: str) -> dict:
    return _workspace_manager.create_workspace(ws_id, name, team_id, owner)

def add_workspace_member(ws_id: str, user: str) -> bool:
    return _workspace_manager.add_workspace_member(ws_id, user)

def list_workspaces(team_id: str) -> list:
    return _workspace_manager.list_workspaces(team_id)

def role_based_access(user_role: str, resource: str, action: str) -> bool:
    """Enforce role-based access control."""
    permissions = {
        'admin': {'query': ['read', 'write', 'delete', 'share', 'delete_workspace'],
                  'team': ['read', 'write', 'manage_members'],
                  'workspace': ['read', 'write', 'manage_members']},
        'maintainer': {'query': ['read', 'write', 'delete', 'share'],
                       'team': ['read'],
                       'workspace': ['read', 'write', 'manage_members']},
        'developer': {'query': ['read', 'write', 'share'],
                      'team': ['read'],
                      'workspace': ['read', 'write']},
        'viewer': {'query': ['read'],
                   'team': ['read'],
                   'workspace': ['read']},
    }
    
    user_perms = permissions.get(user_role, {})
    resource_perms = user_perms.get(resource, [])
    return action in resource_perms

class QueryCommentSystem:
    """Manage comments on queries."""
    def __init__(self):
        self.comments = defaultdict(list)
    
    def add_comment(self, query_id: str, user: str, text: str, line: int = 0) -> dict:
        comment = {
            'id': f"cmt_{int(time.time() * 1000)}",
            'user': user,
            'text': text,
            'line': line,
            'timestamp': int(time.time()),
            'replies': [],
        }
        self.comments[query_id].append(comment)
        return comment
    
    def get_comments(self, query_id: str) -> list:
        return self.comments.get(query_id, [])

_comment_system = QueryCommentSystem()

def add_query_comment(query_id: str, user: str, text: str, line: int = 0) -> dict:
    return _comment_system.add_comment(query_id, user, text, line)

def get_query_comments(query_id: str) -> list:
    return _comment_system.get_comments(query_id)

class QueryReviewSystem:
    """Manage query review workflows."""
    def __init__(self):
        self.reviews = {}
    
    def create_review(self, review_id: str, query_id: str, author: str, reviewers: list) -> dict:
        review = {
            'id': review_id,
            'query_id': query_id,
            'author': author,
            'reviewers': reviewers,
            'status': 'pending',  # pending, approved, rejected, changes_requested
            'created_at': int(time.time()),
            'approvals': [],
        }
        self.reviews[review_id] = review
        return review
    
    def approve_review(self, review_id: str, reviewer: str) -> bool:
        if review_id not in self.reviews:
            return False
        review = self.reviews[review_id]
        if reviewer in review['reviewers'] and reviewer not in review['approvals']:
            review['approvals'].append(reviewer)
            if len(review['approvals']) >= len(review['reviewers']):
                review['status'] = 'approved'
            return True
        return False
    
    def reject_review(self, review_id: str, reviewer: str, reason: str) -> bool:
        if review_id not in self.reviews:
            return False
        review = self.reviews[review_id]
        if reviewer in review['reviewers']:
            review['status'] = 'rejected'
            review['rejection_reason'] = reason
            return True
        return False

_review_system = QueryReviewSystem()

def create_query_review(review_id: str, query_id: str, author: str, reviewers: list) -> dict:
    return _review_system.create_review(review_id, query_id, author, reviewers)

def approve_review(review_id: str, reviewer: str) -> bool:
    return _review_system.approve_review(review_id, reviewer)

def reject_review(review_id: str, reviewer: str, reason: str) -> bool:
    return _review_system.reject_review(review_id, reviewer, reason)

def version_control_integration(query_id: str, version: int, commit_hash: str, author: str, message: str) -> dict:
    """Track query versions with git-like commit info."""
    return {
        'query_id': query_id,
        'version': version,
        'commit_hash': commit_hash,
        'author': author,
        'message': message,
        'timestamp': int(time.time()),
    }

def ci_cd_integration(pipeline_id: str, query_id: str, stage: str, status: str = 'running') -> dict:
    """Integrate with CI/CD pipelines for query validation and deployment."""
    return {
        'pipeline_id': pipeline_id,
        'query_id': query_id,
        'stage': stage,  # lint, test, validate, deploy
        'status': status,  # running, passed, failed
        'timestamp': int(time.time()),
    }

def deployment_pipeline(env: str, query_id: str, version: int, status: str = 'pending') -> dict:
    """Manage deployment across environments (dev, staging, prod)."""
    return {
        'environment': env,
        'query_id': query_id,
        'version': version,
        'status': status,  # pending, deploying, deployed, failed, rolled_back
        'timestamp': int(time.time()),
    }

def team_analytics(team_id: str, events: list) -> dict:
    """Aggregate team activity analytics."""
    if not events:
        return {'total_queries': 0, 'active_users': 0, 'avg_response_time': 0}
    
    users = set()
    total_time = 0
    for event in events:
        users.add(event.get('user', '?'))
        total_time += event.get('response_time', 0)
    
    return {
        'total_queries': len(events),
        'active_users': len(users),
        'avg_response_time_ms': round(total_time / len(events), 2) if events else 0,
        'team_id': team_id,
    }

def organization_schema_registry(org_id: str, schemas: list) -> dict:
    """Centralized schema registry for organization."""
    return {
        'org_id': org_id,
        'schema_count': len(schemas),
        'schemas': [{'name': s.get('name'), 'version': s.get('version', 1)} for s in schemas],
        'last_updated': int(time.time()),
    }

def federated_schema_management(local_schemas: list, remote_schemas: list) -> dict:
    """Manage schemas across multiple services/databases."""
    merged = []
    seen = set()
    
    for schema in local_schemas + remote_schemas:
        key = schema.get('name')
        if key not in seen:
            merged.append(schema)
            seen.add(key)
    
    return {
        'total_schemas': len(merged),
        'local_count': len(local_schemas),
        'remote_count': len(remote_schemas),
        'federated_schemas': merged,
    }

def multi_service_orchestration(services: list, queries: list) -> dict:
    """Orchestrate queries across multiple services."""
    service_queries = defaultdict(list)
    
    for query in (queries or []):
        target = query.get('target', 'unknown')
        for service in (services or []):
            if service.get('name').lower() in target.lower():
                service_queries[service.get('name')].append(query)
    
    return {
        'services': len(services),
        'queries': len(queries),
        'service_distribution': dict(service_queries),
        'timestamp': int(time.time()),
    }
