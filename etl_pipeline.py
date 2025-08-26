import hashlib
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
from pathlib import Path

from models import PDFDocument, ParsedContent, ContentType
from elasticsearch_client import ElasticsearchClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PDFDataProcessor:
    """ETL processor for PDF data ingestion"""
    
    def __init__(self, elasticsearch_client: ElasticsearchClient):
        self.es_client = elasticsearch_client
        
    def process_pdf_data(self, parsed_data: Dict[str, Any], file_path: str) -> Optional[PDFDocument]:
        """
        Process parsed PDF data and create a PDFDocument object
        
        Expected parsed_data format:
        {
            "filename": "document.pdf",
            "total_pages": 10,
            "file_size": 1024000,
            "content": [
                {
                    "type": "paragraph|image|table",
                    "content": "text content or image/table description",
                    "page": 1,
                    "position": {"x": 100, "y": 200, "width": 300, "height": 50},
                    "metadata": {...}
                }
            ]
        }
        """
        try:
            # Generate document ID and checksum
            document_id = str(uuid.uuid4())
            checksum = self._calculate_checksum(parsed_data)
            
            # Process content items
            parsed_content = []
            for item in parsed_data.get("content", []):
                content_type = self._map_content_type(item.get("type", "paragraph"))
                
                parsed_item = ParsedContent(
                    content_type=content_type,
                    content=item.get("content", ""),
                    page_number=item.get("page", 1),
                    position=item.get("position", {"x": 0, "y": 0, "width": 0, "height": 0}),
                    metadata=item.get("metadata", {})
                )
                parsed_content.append(parsed_item)
            
            # Create PDFDocument
            pdf_document = PDFDocument(
                document_id=document_id,
                filename=parsed_data.get("filename", "unknown.pdf"),
                file_path=file_path,
                total_pages=parsed_data.get("total_pages", 1),
                parsed_content=parsed_content,
                file_size=parsed_data.get("file_size", 0),
                checksum=checksum
            )
            
            logger.info(f"Processed PDF document: {pdf_document.filename} with {len(parsed_content)} content items")
            return pdf_document
            
        except Exception as e:
            logger.error(f"Failed to process PDF data: {e}")
            return None
    
    def ingest_document(self, pdf_document: PDFDocument) -> bool:
        """Ingest a processed PDF document into Elasticsearch"""
        try:
            success = self.es_client.index_document(pdf_document)
            if success:
                logger.info(f"Successfully ingested document: {pdf_document.document_id}")
            else:
                logger.error(f"Failed to ingest document: {pdf_document.document_id}")
            return success
        except Exception as e:
            logger.error(f"Error during document ingestion: {e}")
            return False
    
    def process_and_ingest(self, parsed_data: Dict[str, Any], file_path: str) -> Optional[str]:
        """Complete ETL pipeline: process and ingest PDF data"""
        try:
            # Process the data
            pdf_document = self.process_pdf_data(parsed_data, file_path)
            if not pdf_document:
                return None
            
            # Ingest into Elasticsearch
            success = self.ingest_document(pdf_document)
            if success:
                return pdf_document.document_id
            else:
                return None
                
        except Exception as e:
            logger.error(f"ETL pipeline failed: {e}")
            return None
    
    def batch_process(self, data_list: List[Dict[str, Any]], file_paths: List[str]) -> Dict[str, Any]:
        """Process multiple documents in batch"""
        results = {
            "successful": [],
            "failed": [],
            "total": len(data_list)
        }
        
        if len(data_list) != len(file_paths):
            logger.error("Data list and file paths must have the same length")
            return results
        
        for i, (data, file_path) in enumerate(zip(data_list, file_paths)):
            try:
                document_id = self.process_and_ingest(data, file_path)
                if document_id:
                    results["successful"].append({
                        "document_id": document_id,
                        "filename": data.get("filename", f"document_{i}"),
                        "file_path": file_path
                    })
                else:
                    results["failed"].append({
                        "filename": data.get("filename", f"document_{i}"),
                        "file_path": file_path,
                        "error": "Processing or ingestion failed"
                    })
            except Exception as e:
                results["failed"].append({
                    "filename": data.get("filename", f"document_{i}"),
                    "file_path": file_path,
                    "error": str(e)
                })
        
        logger.info(f"Batch processing completed: {len(results['successful'])} successful, {len(results['failed'])} failed")
        return results
    
    def _map_content_type(self, type_str: str) -> ContentType:
        """Map string content type to ContentType enum"""
        type_mapping = {
            "paragraph": ContentType.PARAGRAPH,
            "text": ContentType.PARAGRAPH,
            "image": ContentType.IMAGE,
            "img": ContentType.IMAGE,
            "table": ContentType.TABLE,
            "tab": ContentType.TABLE
        }
        return type_mapping.get(type_str.lower(), ContentType.PARAGRAPH)
    
    def _calculate_checksum(self, data: Dict[str, Any]) -> str:
        """Calculate MD5 checksum for the data"""
        try:
            # Create a consistent string representation
            data_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(data_str.encode('utf-8')).hexdigest()
        except Exception:
            # Fallback to timestamp-based checksum
            return hashlib.md5(str(datetime.now()).encode('utf-8')).hexdigest()

class DataValidator:
    """Validate parsed PDF data before processing"""
    
    @staticmethod
    def validate_pdf_data(data: Dict[str, Any]) -> tuple[bool, List[str]]:
        """Validate parsed PDF data structure"""
        errors = []
        
        # Required fields
        if not data.get("filename"):
            errors.append("Missing required field: filename")
        
        if not data.get("content"):
            errors.append("Missing required field: content")
        elif not isinstance(data["content"], list):
            errors.append("Content must be a list")
        
        # Validate content items
        for i, item in enumerate(data.get("content", [])):
            if not isinstance(item, dict):
                errors.append(f"Content item {i} must be a dictionary")
                continue
            
            if not item.get("content"):
                errors.append(f"Content item {i} missing content field")
            
            if "page" in item and not isinstance(item["page"], int):
                errors.append(f"Content item {i} page must be an integer")
            
            if "position" in item and not isinstance(item["position"], dict):
                errors.append(f"Content item {i} position must be a dictionary")
        
        # Optional field validation
        if "total_pages" in data and not isinstance(data["total_pages"], int):
            errors.append("total_pages must be an integer")
        
        if "file_size" in data and not isinstance(data["file_size"], int):
            errors.append("file_size must be an integer")
        
        return len(errors) == 0, errors

# Example usage and test data
def create_sample_data() -> Dict[str, Any]:
    """Create sample parsed PDF data for testing"""
    return {
        "filename": "sample_document.pdf",
        "total_pages": 3,
        "file_size": 1024000,
        "content": [
            {
                "type": "paragraph",
                "content": "This is the first paragraph of the document. It contains important information about the topic.",
                "page": 1,
                "position": {"x": 72, "y": 720, "width": 450, "height": 24},
                "metadata": {"font_size": 12, "font_family": "Arial"}
            },
            {
                "type": "table",
                "content": "Table showing quarterly revenue: Q1: $100K, Q2: $150K, Q3: $200K, Q4: $180K",
                "page": 1,
                "position": {"x": 72, "y": 650, "width": 450, "height": 100},
                "metadata": {"rows": 5, "columns": 2}
            },
            {
                "type": "image",
                "content": "Chart displaying quarterly growth trends with ascending line graph",
                "page": 2,
                "position": {"x": 100, "y": 500, "width": 400, "height": 300},
                "metadata": {"image_type": "chart", "alt_text": "Growth chart"}
            },
            {
                "type": "paragraph",
                "content": "The analysis shows consistent growth throughout the year with a slight dip in Q4.",
                "page": 2,
                "position": {"x": 72, "y": 450, "width": 450, "height": 24},
                "metadata": {"font_size": 12, "font_family": "Arial"}
            }
        ]
    }

if __name__ == "__main__":
    # Example usage
    from elasticsearch_client import ElasticsearchClient
    
    # Initialize components
    es_client = ElasticsearchClient()
    processor = PDFDataProcessor(es_client)
    
    # Create and process sample data
    sample_data = create_sample_data()
    
    # Validate data
    is_valid, errors = DataValidator.validate_pdf_data(sample_data)
    if not is_valid:
        print(f"Validation errors: {errors}")
    else:
        # Process and ingest
        document_id = processor.process_and_ingest(sample_data, "/path/to/sample_document.pdf")
        if document_id:
            print(f"Successfully processed document with ID: {document_id}")
        else:
            print("Failed to process document")
