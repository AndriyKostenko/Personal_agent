from qdrant_client import QdrantClient
import json

# Подключаемся к локальной папке
client = QdrantClient(path="../qdrant_db")
collection_name = "knowledge_base_lc"

# Получаем информацию о коллекции
info = client.get_collection(collection_name)
print(f"Number of points: {info.points_count}\n")

# Извлекаем первые 3 записи (с векторами и метаданными)
points, _ = client.scroll(
    collection_name=collection_name,
    limit=3,
    with_payload=True,
    with_vectors=False # Ставим False, чтобы консоль не заполнилась тысячами чисел
)

for point in points:
    print(f"ID: {point.id}")
    print("Payload:")
    print(json.dumps(point.payload, indent=2, ensure_ascii=False))
    print("-" * 50)
