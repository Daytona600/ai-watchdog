from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

class Database:
    def __init__(self, url="http://localhost:6333", collection_name="watchdog"):
        self.client = QdrantClient(url=url)
        self.collection_name = collection_name

    def create_collection(self):
        if not self.client.get_collections().collections:
            self.client.create_collection(collection_name=self.collection_name)

    def add_stage(self, stage_name, description):
        data = {
            "stage_name": stage_name,
            "description": description
        }
        self.client.upsert(
            collection_name=self.collection_name,
            points=[data]
        )

    def get_last_stage(self):
        filter = Filter(
            must=[
                FieldCondition(key="stage_name", match=MatchValue(value=None))
            ]
        )
        result = self.client.search(
            collection_name=self.collection_name,
            query_vector=[0] * 128,  # Placeholder vector
            limit=1,
            filter=filter
        )
        if result:
            return result[0].payload
        return None

    def update_stage(self, stage_name, description):
        data = {
            "stage_name": stage_name,
            "description": description
        }
        self.client.upsert(
            collection_name=self.collection_name,
            points=[data]
        )
