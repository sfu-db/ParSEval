{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "INTEGER"
    },
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
                          "kind": "OTHER_FUNCTION",
                          "operator": "CURRENT_TIMESTAMP",
                          "type": "TIMESTAMP",
                          "operands": []
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
                          "index": 10,
                          "name": "$10",
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
  "id": "6",
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
                "kind": "INPUT_REF",
                "index": 6,
                "name": "$6",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "gold",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
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
                "index": 3,
                "name": "$3",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "OWNER",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "5",
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
                "index": 1,
                "name": "$1",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 8,
                "name": "$8",
                "type": "INTEGER"
              }
            ]
          },
          "id": "4",
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
                    "index": 5,
                    "name": "$5",
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
                  "table": "disp",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "card",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "client",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}