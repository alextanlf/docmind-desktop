from uuid import uuid4

from app.schemas.web_search import NormalizedSearchResult
from app.storage.models import RepositoryRecord


def test_selected_results_create_awaiting_confirmation_batch(client, auth_headers):
    with client.app.state.database.session() as database:
        repository = RepositoryRecord(name="Search")
        database.add(repository)
        database.flush()
        repository_id = repository.id
    request_id, session_id, message_id = (str(uuid4()) for _ in range(3))
    store = client.app.state.search_service.run_store
    run = store.create(request_id=request_id, session_id=session_id, user_message_id=message_id, query="q")
    store.complete(run.id, [NormalizedSearchResult(rank=1, canonicalUrl="https://example.test/a", title="A", snippet="snippet", content="content")])
    result = store.results(run.id)[0]
    response = client.post(f"/api/web-search/runs/{run.id}/import-batch", headers=auth_headers, json={"repositoryId": repository_id, "resultIds": [result.id]})
    assert response.status_code == 201
    assert response.json()["state"] == "awaiting_confirmation"
    assert response.json()["sourceKind"] == "search_results"
