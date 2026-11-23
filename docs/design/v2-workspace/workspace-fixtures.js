const workspaceDatasets = {
  "financial-services": {
    "name": "Financial Services",
    "status": "Local sources",
    "sources": [
      {
        "id": "workbook",
        "preparedId": "workbook",
        "title": "MockUp Data.xlsx",
        "kind": "Workbook · 17 sheets",
        "locator": "Customers · RiskRating",
        "excerpt": "The workbook records RiskRating at the customer business snapshot.",
        "detail": "Review the rating scale, customer population and AML policy before binding this field.",
        "label": "Metadata only"
      },
      {
        "id": "looker",
        "preparedId": "looker-crosswalk",
        "title": "Looker metadata",
        "kind": "Semantic definitions · bank_trino",
        "locator": "corporate_customer.customer_risk_rating",
        "excerpt": "This Looker field describes corporate customer risk.",
        "detail": "Business definitions provide meaning. They do not establish an identity link between separate datasets.",
        "label": "Metadata only"
      },
      {
        "id": "jade",
        "preparedId": "jade-adverse-media",
        "title": "Jade Logistics adverse-media report",
        "kind": "Synthetic screening report · Separate customer scope",
        "locator": "adverse1536_NEWS-CORP0006700_jade-logistics-pte-ltd.md · Reported relationship",
        "quote": "Public records link the company to AHMAD BIN OSMAN, who holds a controlling or directorship interest.",
        "detail": "The report is unverified screening evidence. Jade Logistics has no exact name match in the workbook customer roster.",
        "label": "Scoped evidence"
      }
    ],
    "trace": [
      {
        "id": "jade",
        "kind": "doc",
        "label": "Adverse-media report",
        "detail": "Jade Logistics · Scoped evidence",
        "text": "The synthetic report mentions Ahmad. It belongs to the separate customer scope.",
        "source": "jade"
      },
      {
        "id": "mentioned-person",
        "kind": "person",
        "label": "Ahmad Bin Osman",
        "detail": "Reported relationship",
        "text": "The report mentions this person. The V1 demo records a separate accepted relationship.",
        "source": "jade",
        "relation": "Mentions"
      },
      {
        "id": "customer",
        "kind": "party",
        "label": "Jade Logistics",
        "detail": "V1 demo customer record",
        "text": "The workbook and the V1 demo use independent customer populations.",
        "relation": "Accepted demo link"
      }
    ],
    "definitions": [
      {
        "id": "risk",
        "name": "Customer risk rating",
        "meaning": "The risk rating supplied for a customer at a workbook business snapshot.",
        "grain": "Customer snapshot",
        "sourceField": "Customers.RiskRating",
        "field": "corporate_customer.customer_risk_rating",
        "relation": "Candidate · needs review",
        "limit": "Confirm the corporate population, rating scale and AML policy."
      },
      {
        "id": "industry",
        "name": "Industry code",
        "meaning": "The industry code supplied for a customer.",
        "grain": "Customer snapshot",
        "sourceField": "Customers.IndustryCode",
        "field": "corporate_customer.industry",
        "relation": "Candidate · needs review",
        "limit": "Decode the industry code system before mapping to corporate sector labels."
      },
      {
        "id": "segment",
        "name": "Customer segment",
        "meaning": "The customer segment recorded at the workbook snapshot.",
        "grain": "Customer snapshot",
        "sourceField": "Customers.CustomerSegment",
        "field": "corporate_customer.segment",
        "relation": "Candidate · needs review",
        "limit": "Compare corporate segment vocabularies and snapshot dates."
      },
      {
        "id": "amount",
        "name": "Transaction amount",
        "meaning": "An amount supplied for a current or savings account transaction.",
        "grain": "Transaction per business snapshot · source currency",
        "sourceField": "CASA_Txn.Amount",
        "field": "enterprise_customer_risk.deposits_sgd",
        "relation": "Unsupported mapping",
        "limit": "The workbook has event amounts. The Looker measure describes deposit holdings at a snapshot."
      }
    ],
    "proposal": {
      "title": "Customer risk rating field alignment",
      "scope": "Workbook definitions · Financial Services",
      "source": "workbook",
      "mapping": "risk_rating",
      "before": "Exact, approved field binding",
      "after": "Candidate · needs review",
      "detail": "Customers.RiskRating → corporate_customer.customer_risk_rating",
      "rationale": "Preserve the field as a candidate. Verify rating scale, population and AML policy before approving an exact mapping.",
      "base": "Workbook header and Looker field",
      "revision": "Crosswalk review decision",
      "ref": "finance_workbook_demo/crosswalk/risk_rating/preview/revision/2",
      "effect": "The preview publishes the decision to retain the candidate. The mapping remains unresolved."
    },
    "entity": {
      "id": "financial-services:entity:jade-logistics",
      "name": "Jade Logistics",
      "scope": "Existing V1 demo · Separate customer scope",
      "subject": "Commercial banking",
      "detail": "Workbook linkage unavailable",
      "facts": [
        [
          "Deposit balance",
          "SGD 1,301,937.93"
        ],
        [
          "Recorded risk",
          "Medium"
        ],
        [
          "Direct documents",
          "1"
        ]
      ],
      "evidence": [
        [
          "Accepted demo relationship",
          "Ahmad Bin Osman"
        ],
        [
          "Workbook customer identity",
          "Unresolved"
        ],
        [
          "Monthly consumption series",
          "Unavailable in this preview"
        ]
      ],
      "factSource": "V1 structured demo record · Jade Logistics",
      "answer": "The V1 demo records a synthetic balance of SGD 1,301,937.93 and an accepted link to Ahmad. The screening report mentions him.",
      "reference": "jade",
      "conclusions": {
        "pending": "The workbook risk alignment awaits review. The workbook did not supply these customer facts.",
        "current": "The published preview retains the workbook risk field as a candidate. The workbook did not supply these customer facts.",
        "baseline": "The baseline contains the workbook header and Looker definition. Workbook identity remains unresolved."
      }
    }
  },
  "manufacturing": {
    "name": "Manufacturing",
    "status": "Synthetic documents",
    "sources": [
      {
        "id": "amendment",
        "preparedId": "MFG-003",
        "title": "Delivery amendment to MFG-002",
        "kind": "MFG-003 · DOCX · Rev 2",
        "locator": "MFG-003 · Page 1 §2 · Effective 2024-05-30",
        "quote": "Effective 2024-05-30, the promised delivery date for SalesOrderID 51131 changes from 2024-06-10 to 2024-06-17.",
        "detail": "This amendment replaces only the delivery-promise clause. The operational due date and dispatch date remain separate.",
        "label": "Draft evidence"
      },
      {
        "id": "original",
        "preparedId": "MFG-002",
        "title": "Product order schedule",
        "kind": "MFG-002 · DOCX · Rev 1",
        "locator": "MFG-002 · Page 1 §3 · Effective 2024-05-29",
        "quote": "The synthetic promised delivery date for SalesOrderID 51131 is 2024-06-10.",
        "detail": "Page 1 §2 names order 51131, line 36824 and 16 units. The order due date provides operational context.",
        "label": "Baseline evidence"
      },
      {
        "id": "orders",
        "title": "AdventureWorks order records",
        "kind": "Structured order and order-line records",
        "locator": "SalesOrderHeader 51131 · SalesOrderDetail 36824",
        "excerpt": "The sales order header records 2024-06-05 as ShipDate. The selected sales order detail records 16 ordered units.",
        "detail": "The records supply neither a line-level shipment quantity nor customer receipt evidence.",
        "label": "Structured evidence"
      }
    ],
    "trace": [
      {
        "id": "amendment",
        "kind": "doc",
        "label": "Delivery amendment",
        "detail": "MFG-003 · Page 1 §2",
        "text": "The clause changes the promised delivery date from June 10 to June 17.",
        "source": "amendment"
      },
      {
        "id": "promise",
        "kind": "mapping",
        "label": "Delivery promise",
        "detail": "Order scope",
        "text": "The obligation concerns delivery. The header records dispatch.",
        "source": "amendment"
      },
      {
        "id": "orders",
        "kind": "record",
        "label": "Order and line records",
        "detail": "Order 51131 · Detail 36824",
        "text": "The header records dispatch on June 5. The line records 16 ordered units.",
        "source": "orders",
        "relation": "Order context"
      }
    ],
    "definitions": [
      {
        "id": "promise",
        "name": "Promised delivery date",
        "meaning": "The calendar date by which an agreement promises delivery.",
        "grain": "Order obligation",
        "field": "Sales.SalesOrderHeader.DueDate",
        "relation": "Context only",
        "limit": "An amendment can change the promise independently of the operational due date."
      },
      {
        "id": "quantity",
        "name": "Ordered quantity",
        "meaning": "The number of product units requested on a sales order line.",
        "grain": "Order line · units",
        "field": "Sales.SalesOrderDetail.OrderQty",
        "relation": "Source field",
        "limit": "Dispatch and receipt require separate evidence."
      },
      {
        "id": "warranty",
        "name": "Warranty duration",
        "meaning": "Coverage period measured from the start event named in an agreement.",
        "grain": "Agreement term",
        "relation": "Unmapped",
        "limit": "The prepared model has no equivalent operational field."
      }
    ],
    "proposal": {
      "title": "Delivery date for order 51131",
      "scope": "Westside Plaza · Detail 36824",
      "source": "amendment",
      "mapping": "delivery-vs-due-date",
      "before": "Expected dispatch date",
      "after": "Promised delivery date",
      "detail": "2024-06-17 · Order 51131",
      "rationale": "The clause changes the promised delivery date. ShipDate records dispatch at order-header scope.",
      "base": "Original delivery schedule",
      "revision": "Amended delivery promise",
      "ref": "adventureworks_demo/customer/29641/order/51131/revision/2",
      "effect": "Revision 2 supplies the amended promise for a business date from 2024-05-30."
    },
    "entity": {
      "id": "manufacturing:entity:westside-plaza",
      "name": "Westside Plaza",
      "scope": "AdventureWorks demo · Customer 29641",
      "subject": "Order 51131",
      "detail": "Line 36824 · Touring-1000 Yellow, 46 · BK-T79Y-46",
      "facts": [
        [
          "Ordered quantity",
          "16 units"
        ],
        [
          "Header dispatch",
          "5 Jun 2024"
        ],
        [
          "Delivery result",
          "Unknown"
        ]
      ],
      "evidence": [
        [
          "Line shipped quantity",
          "Unknown"
        ],
        [
          "Operational due date",
          "10 Jun 2024"
        ],
        [
          "Recorded customer delivery",
          "Unknown"
        ]
      ],
      "factSource": "SalesOrderDetail 36824 · SalesOrderHeader 51131 · Observation cutoff 2024-06-17",
      "answer": "The order line records 16 ordered units. The order header records dispatch on 5 Jun 2024. The line shipped quantity and customer delivery remain unknown.",
      "reference": "orders",
      "conclusions": {
        "pending": "The amendment awaits publication. The current promised date is withheld.",
        "current": "The amended agreement promises delivery by 17 Jun 2024.",
        "baseline": "The original agreement promised delivery by 10 Jun 2024."
      },
      "baselineSource": "original",
      "currentSource": "amendment"
    }
  }
};
