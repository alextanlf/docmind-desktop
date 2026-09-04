

def test_production_wires_durable_mutation_store(client):
    assert client.app.state.distillation_service.mutation_store is client.app.state.document_mutation_store
    assert client.app.state.distillation_service.document_saver is not None
