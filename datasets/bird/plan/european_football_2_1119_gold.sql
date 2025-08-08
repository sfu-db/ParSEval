{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "VARCHAR"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN_OR_EQUAL",
        "operator": ">=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "DIVIDE",
            "operator": "/",
            "type": "REAL",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "REAL",
                "operands": [
                  {
                    "kind": "MINUS",
                    "operator": "-",
                    "type": "INTEGER",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "JULIANDAY",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "DATE",
                            "operands": [
                              {
                                "kind": "LITERAL",
                                "value": "now",
                                "type": "CHAR",
                                "nullable": false,
                                "precision": 3
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "JULIANDAY",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "DATE",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 4,
                                "name": "$4",
                                "type": "VARCHAR"
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
                "kind": "CASE",
                "operator": "CASE",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": 365,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      },
                      {
                        "kind": "LITERAL",
                        "value": 0,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": "NULL",
                    "type": "INTEGER",
                    "nullable": true,
                    "precision": 10
                  },
                  {
                    "kind": "LITERAL",
                    "value": 365,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          },
          {
            "kind": "LITERAL",
            "value": 35,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Player",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}