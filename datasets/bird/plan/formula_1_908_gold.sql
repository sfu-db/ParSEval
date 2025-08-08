{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": null,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "VARCHAR"
        },
        {
          "column": 1,
          "type": "VARCHAR"
        },
        {
          "column": 2,
          "type": "DATE"
        }
      ],
      "aggs": [],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 13,
              "name": "$13",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 4,
              "name": "$4",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 14,
              "name": "$14",
              "type": "DATE"
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 10,
                    "name": "$10",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 2017,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              "variableset": "[]",
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 12,
                        "name": "$12",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "circuits",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "races",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}