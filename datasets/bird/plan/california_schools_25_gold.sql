{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 18,
      "name": "$18",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "DIVIDE",
                "operator": "/",
                "type": "FLOAT",
                "operands": [
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "REAL",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 30,
                        "name": "$30",
                        "type": "FLOAT"
                      }
                    ]
                  },
                  {
                    "kind": "CASE",
                    "operator": "CASE",
                    "type": "FLOAT",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 29,
                            "name": "$29",
                            "type": "FLOAT"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0.0,
                            "type": "FLOAT",
                            "nullable": false,
                            "precision": 15
                          }
                        ]
                      },
                      {
                        "kind": "LITERAL",
                        "value": "NULL",
                        "type": "FLOAT",
                        "nullable": true,
                        "precision": 15
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 29,
                        "name": "$29",
                        "type": "FLOAT"
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 0.1,
                "type": "DECIMAL",
                "nullable": false,
                "precision": 2
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
                "index": 10,
                "name": "$10",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 0,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
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
                "index": 0,
                "name": "$0",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 11,
                "name": "$11",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "satscores",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "frpm",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}