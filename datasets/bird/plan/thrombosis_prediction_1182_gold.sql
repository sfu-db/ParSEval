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
                  "value": "%Y",
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
                  "value": "%Y",
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
                          "index": 2,
                          "name": "$2",
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
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "DOUBLE",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 15,
                    "name": "$15",
                    "type": "FLOAT"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 8.4,
                "type": "DOUBLE",
                "nullable": false,
                "precision": 15
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 8,
                "name": "$8",
                "type": "DATE"
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "1991-10-21",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 10
                  }
                ]
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
              "table": "Laboratory",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}