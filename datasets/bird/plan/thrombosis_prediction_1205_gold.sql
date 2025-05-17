{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "MINUS",
      "operator": "-",
      "type": "DECIMAL",
      "operands": [
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "OTHER_FUNCTION",
              "operator": "STRFTIME",
              "type": "VARCHAR",
              "operands": [
                {
                  "kind": "LITERAL",
                  "value": "%d",
                  "type": "CHAR",
                  "nullable": false,
                  "precision": 2
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "TIMESTAMP",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 8,
                          "name": "$8",
                          "type": "DATE"
                        }
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "OTHER_FUNCTION",
              "operator": "STRFTIME",
              "type": "VARCHAR",
              "operands": [
                {
                  "kind": "LITERAL",
                  "value": "%d",
                  "type": "CHAR",
                  "nullable": false,
                  "precision": 2
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "CAST",
                      "operator": "CAST",
                      "type": "TIMESTAMP",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 4,
                          "name": "$4",
                          "type": "DATE"
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
            "index": 0,
            "name": "$0",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 821298,
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
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Examination",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}