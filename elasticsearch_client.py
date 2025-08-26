import logging
from elasticsearch import Elasticsearch
from typing import List, Dict, Any, Optional
import json
from datetime import datetime
from config import ELASTICSEARCH_URL, ELASTICSEARCH_INDEX_PREFIX
from models import PDFDocument, SearchQuery, SearchResult, SearchResponse, ContentType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ElasticsearchClient:
    def __init__(self):
        try:
            self.client = Elasticsearch([ELASTICSEARCH_URL])
            self.index_name = f"{ELASTICSEARCH_INDEX_PREFIX}_documents"
            self._create_index_if_not_exists()
            logger.info("Elasticsearch client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Elasticsearch client: {e}")
            raise

    def _create_index_if_not_exists(self):
        """Create the Elasticsearch index with proper mappings if it doesn't exist"""
        if not self.client.indices.exists(index=self.index_name):
            mapping = {
                "mappings": {
                    "properties": {
                        "document_id": {"type": "keyword"},
                        "filename": {"type": "text", "analyzer": "standard"},
                        "file_path": {"type": "keyword"},
                        "total_pages": {"type": "integer"},
                        "file_size": {"type": "long"},
                        "checksum": {"type": "keyword"},
                        "upload_timestamp": {"type": "date"},
                        "content": {
                            "type": "nested",
                            "properties": {
                                "content_type": {"type": "keyword"},
                                "content": {
                                    "type": "text",
                                    "analyzer": "standard",
                                    "fields": {
                                        "keyword": {"type": "keyword"}
                                    }
                                },
                                "page_number": {"type": "integer"},
                                "position": {
                                    "properties": {
                                        "x": {"type": "float"},
                                        "y": {"type": "float"},
                                        "width": {"type": "float"},
                                        "height": {"type": "float"}
                                    }
                                },
                                "metadata": {"type": "object", "enabled": False}
                            }
                        }
                    }
                },
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "analysis": {
                        "analyzer": {
                            "custom_analyzer": {
                                "type": "custom",
                                "tokenizer": "standard",
                                "filter": ["lowercase", "stop", "snowball"]
                            }
                        }
                    }
                }
            }
            
            self.client.indices.create(index=self.index_name, body=mapping)
            logger.info(f"Created Elasticsearch index: {self.index_name}")

    def index_document(self, pdf_document: PDFDocument) -> bool:
        """Index a PDF document with all its parsed content"""
        try:
            # Prepare document for indexing
            doc_data = {
                "document_id": pdf_document.document_id,
                "filename": pdf_document.filename,
                "file_path": pdf_document.file_path,
                "total_pages": pdf_document.total_pages,
                "file_size": pdf_document.file_size,
                "checksum": pdf_document.checksum,
                "upload_timestamp": pdf_document.upload_timestamp.isoformat(),
                "content": [
                    {
                        "content_type": content.content_type.value,
                        "content": content.content,
                        "page_number": content.page_number,
                        "position": content.position,
                        "metadata": content.metadata or {}
                    }
                    for content in pdf_document.parsed_content
                ]
            }
            
            # Index the document
            response = self.client.index(
                index=self.index_name,
                id=pdf_document.document_id,
                body=doc_data
            )
            
            logger.info(f"Indexed document {pdf_document.document_id}: {response['result']}")
            return response['result'] in ['created', 'updated']
            
        except Exception as e:
            logger.error(f"Failed to index document {pdf_document.document_id}: {e}")
            return False

    def search_documents(self, search_query: SearchQuery) -> SearchResponse:
        """Search across indexed PDF documents"""
        try:
            start_time = datetime.now()
            
            # Build Elasticsearch query
            query = self._build_search_query(search_query)
            
            # Execute search
            response = self.client.search(
                index=self.index_name,
                body=query,
                size=search_query.limit,
                from_=search_query.offset
            )
            
            # Process results
            search_results = self._process_search_results(response)
            
            end_time = datetime.now()
            query_time_ms = (end_time - start_time).total_seconds() * 1000
            
            return SearchResponse(
                results=search_results,
                total_hits=response['hits']['total']['value'],
                query_time_ms=query_time_ms,
                page=search_query.offset // search_query.limit + 1,
                per_page=search_query.limit
            )
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return SearchResponse(
                results=[],
                total_hits=0,
                query_time_ms=0,
                page=1,
                per_page=search_query.limit
            )

    def _build_search_query(self, search_query: SearchQuery) -> Dict[str, Any]:
        """Build Elasticsearch query from search parameters"""
        
        # Base nested query for content search
        nested_query = {
            "nested": {
                "path": "content",
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": search_query.query,
                                    "fields": ["content.content^2", "filename"],
                                    "type": "best_fields",
                                    "fuzziness": "AUTO"
                                }
                            }
                        ]
                    }
                },
                "inner_hits": {
                    "highlight": {
                        "fields": {
                            "content.content": {
                                "pre_tags": ["<mark>"],
                                "post_tags": ["</mark>"],
                                "fragment_size": 150,
                                "number_of_fragments": 3
                            }
                        }
                    }
                }
            }
        }
        
        # Add content type filter if specified
        if search_query.content_types:
            content_type_filter = {
                "terms": {
                    "content.content_type": [ct.value for ct in search_query.content_types]
                }
            }
            nested_query["nested"]["query"]["bool"]["must"].append(content_type_filter)
        
        # Add page number filter if specified
        if search_query.page_numbers:
            page_filter = {
                "terms": {
                    "content.page_number": search_query.page_numbers
                }
            }
            nested_query["nested"]["query"]["bool"]["must"].append(page_filter)
        
        # Build main query
        main_query = {
            "query": {
                "bool": {
                    "must": [nested_query]
                }
            },
            "sort": [
                {"_score": {"order": "desc"}},
                {"upload_timestamp": {"order": "desc"}}
            ]
        }
        
        # Add document ID filter if specified
        if search_query.document_ids:
            doc_filter = {
                "terms": {
                    "document_id": search_query.document_ids
                }
            }
            main_query["query"]["bool"]["must"].append(doc_filter)
        
        return main_query

    def _process_search_results(self, response: Dict[str, Any]) -> List[SearchResult]:
        """Process Elasticsearch response into SearchResult objects"""
        results = []
        
        for hit in response['hits']['hits']:
            source = hit['_source']
            score = hit['_score']
            
            # Get inner hits (nested content matches)
            if 'inner_hits' in hit and 'content' in hit['inner_hits']:
                for inner_hit in hit['inner_hits']['content']['hits']['hits']:
                    content_data = inner_hit['_source']
                    
                    # Extract highlight if available
                    highlight = None
                    if 'highlight' in inner_hit and 'content.content' in inner_hit['highlight']:
                        highlight = ' ... '.join(inner_hit['highlight']['content.content'])
                    
                    result = SearchResult(
                        document_id=source['document_id'],
                        filename=source['filename'],
                        content_type=ContentType(content_data['content_type']),
                        content=content_data['content'],
                        page_number=content_data['page_number'],
                        score=score,
                        highlight=highlight
                    )
                    results.append(result)
            else:
                # Fallback if no inner hits
                result = SearchResult(
                    document_id=source['document_id'],
                    filename=source['filename'],
                    content_type=ContentType.PARAGRAPH,  # Default
                    content=source.get('filename', ''),
                    page_number=1,
                    score=score,
                    highlight=None
                )
                results.append(result)
        
        return results

    def delete_document(self, document_id: str) -> bool:
        """Delete a document from the index"""
        try:
            response = self.client.delete(
                index=self.index_name,
                id=document_id
            )
            logger.info(f"Deleted document {document_id}: {response['result']}")
            return response['result'] == 'deleted'
        except Exception as e:
            logger.error(f"Failed to delete document {document_id}: {e}")
            return False

    def health_check(self) -> Dict[str, Any]:
        """Check Elasticsearch cluster health"""
        try:
            health = self.client.cluster.health()
            indices_info = self.client.indices.stats(index=self.index_name)
            
            return {
                "status": "healthy",
                "cluster_name": health['cluster_name'],
                "status": health['status'],
                "number_of_nodes": health['number_of_nodes'],
                "documents_count": indices_info['_all']['total']['docs']['count'],
                "index_size": indices_info['_all']['total']['store']['size_in_bytes']
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }
