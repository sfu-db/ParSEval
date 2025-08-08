{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "INTEGER"
    }
  ],
  "aggs": [],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "GREATER_THAN_OR_EQUAL",
                "operator": ">=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "DATE"
                  },
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "1987-07-06",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LESS_THAN_OR_EQUAL",
                "operator": "<=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "DATE"
                  },
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "1996-01-31",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 3,
                    "name": "$3",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 30,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 4,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Laboratory",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}