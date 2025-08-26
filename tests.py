import pytest
import json
from fastapi.testclient import TestClient
from api import app
from models import SearchQuery, ContentType

client = TestClient(app)

# Test data
sample_user = {
    "username": "testuser",
    "email": "test@example.com",
    "password": "testpassword123",
    "full_name": "Test User"
}

sample_pdf_data = {
    "filename": "test_document.pdf",
    "total_pages": 2,
    "file_size": 1024000,
    "content": [
        {
            "type": "paragraph",
            "content": "This is a test paragraph with important information.",
            "page": 1,
            "position": {"x": 72, "y": 720, "width": 450, "height": 24},
            "metadata": {"font_size": 12}
        },
        {
            "type": "table",
            "content": "Test table with data: Column A, Column B, Row 1, Row 2",
            "page": 1,
            "position": {"x": 72, "y": 650, "width": 450, "height": 100},
            "metadata": {"rows": 2, "columns": 2}
        }
    ]
}

def test_health_check():
    """Test health check endpoint"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "timestamp" in data

def test_register_user():
    """Test user registration"""
    response = client.post("/auth/register", json=sample_user)
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == sample_user["username"]

def test_login_user():
    """Test user login"""
    # First register the user
    client.post("/auth/register", json=sample_user)
    
    # Then login
    response = client.post(
        "/auth/login",
        data={"username": sample_user["username"], "password": sample_user["password"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    return data["access_token"]

def test_search_documents():
    """Test document search"""
    # Setup: register, login, upload document
    token = test_login_user()
    headers = {"Authorization": f"Bearer {token}"}
    
    files = {"file": ("test.json", json.dumps(sample_pdf_data), "application/json")}
    client.post("/documents/upload", files=files, headers=headers)
    
    # Test search
    response = client.get("/search?q=test&limit=5", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total_hits" in data

def test_advanced_search():
    """Test advanced search"""
    # Setup
    token = test_login_user()
    headers = {"Authorization": f"Bearer {token}"}
    
    search_query = {
        "query": "test paragraph",
        "content_types": ["paragraph"],
        "limit": 10,
        "offset": 0
    }
    
    response = client.post("/search", json=search_query, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "results" in data

def test_unauthorized_access():
    """Test unauthorized access to protected endpoints"""
    response = client.get("/search?q=test")
    assert response.status_code == 401

def test_invalid_search_query():
    """Test invalid search query"""
    token = test_login_user()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get("/search?q=", headers=headers)
    assert response.status_code == 400

def test_metrics_endpoint():
    """Test metrics endpoint"""
    token = test_login_user()
    headers = {"Authorization": f"Bearer {token}"}
    
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "elasticsearch" in data
    assert "api" in data

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
