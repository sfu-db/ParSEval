{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "MINUS",
      "operator": "-",
      "type": "INTEGER",
      "operands": [
        {
          "kind": "SCALAR_QUERY",
          "operator": "$SCALAR_QUERY",
          "operands": [],
          "query": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 11,
                  "name": "$11",
                  "type": "INTEGER"
                }
              ],
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "LIKE",
                    "operator": "LIKE",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 2,
                        "name": "$2",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Emil Blonsky",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 12
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "1",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "superhero",
                      "id": "0",
                      "inputs": []
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "kind": "SCALAR_QUERY",
          "operator": "$SCALAR_QUERY",
          "operands": [],
          "query": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 11,
                  "name": "$11",
                  "type": "INTEGER"
                }
              ],
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "LIKE",
                    "operator": "LIKE",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 2,
                        "name": "$2",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Charles Chandler",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 16
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "4",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "superhero",
                      "id": "3",
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
  ],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalValues",
      "values": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ],
      "id": "6",
      "inputs": []
    }
  ]
}