{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 5,
      "name": "$5",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "VARCHAR"
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
                "kind": "OTHER_FUNCTION",
                "operator": "UDATE",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "TIMESTAMP",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "SUBSTR",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          },
                          {
                            "kind": "LITERAL",
                            "value": 10,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
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
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "2019-11-01",
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
                "kind": "OTHER_FUNCTION",
                "operator": "UDATE",
                "type": "DATE",
                "operands": [
                  {
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "TIMESTAMP",
                    "operands": [
                      {
                        "kind": "OTHER_FUNCTION",
                        "operator": "SUBSTR",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 2,
                            "name": "$2",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 1,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          },
                          {
                            "kind": "LITERAL",
                            "value": 10,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
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
                "type": "DATE",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "2020-03-31",
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
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "event",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}