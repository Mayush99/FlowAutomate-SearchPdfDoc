# PDF Search Engine

A comprehensive search engine for parsed PDF content including paragraphs, images, and tables using Elasticsearch.

## Features

- ETL pipeline for ingesting parsed PDF data
- Secure REST API with JWT authentication
- Full-text search across paragraphs, images, and tables
- Real-time indexing and search
- Comprehensive logging and error handling

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start Elasticsearch (Docker recommended):
```bash
docker run -d --name elasticsearch -p 9200:9200 -e "discovery.type=single-node" -e "xpack.security.enabled=false" docker.elastic.co/elasticsearch/elasticsearch:8.12.0
```

3. Set environment variables in `.env` file

4. Run the application:
```bash
python main.py
```

## API Endpoints

- `POST /auth/register` - Register new user
- `POST /auth/login` - Login and get JWT token
- `POST /documents/upload` - Upload parsed PDF data
- `GET /search` - Search across all content
- `GET /health` - Health check

## Security Features

- JWT token-based authentication
- Rate limiting
- Input validation and sanitization
- CORS protection
- Secure headers
