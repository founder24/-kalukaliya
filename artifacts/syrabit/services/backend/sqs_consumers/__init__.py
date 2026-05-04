"""SQS-triggered Lambda handler package (Task #332).

Each submodule defines a ``handler(event, context)`` referenced by
the corresponding entry in ``infra/aws/lambda-workers.tf`` (see the
``sqs_consumers`` map). The Lambda image (``ecr.tf``
``sqs-consumers-latest``) ships this package together with the
backend tree on PYTHONPATH so the handlers can call into the same
business logic the FastAPI process calls today.

All handlers share these conventions:

* Receive an SQS-style event (``Records: [{messageId, body, ...}]``)
  and return a payload of the form
  ``{"batchItemFailures": [{"itemIdentifier": "<messageId>"}]}``
  so a single bad message does not poison the whole batch — the
  Lambda event source mapping is configured with
  ``ReportBatchItemFailures`` in lambda-workers.tf.
* Parse ``body`` as JSON; reject non-JSON messages by adding their
  messageId to ``batchItemFailures``.
* Run the per-message body inside ``asyncio.run`` so each handler is
  a thin sync shell around the existing async backend code.
"""
