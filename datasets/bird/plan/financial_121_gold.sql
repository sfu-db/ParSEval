{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
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
                "value": "east Bohemia",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN_OR_EQUAL",
            "operator": ">=",
            "type": "BOOLEAN",
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
                            "index": 3,
                            "name": "$3",
                            "type": "DATE"
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": "1995",
                "type": "CHAR",
                "nullable": false,
                "precision": 4
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
                            "index": 3,
                            "name": "$3",
                            "type": "DATE"
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": "2000",
                "type": "CHAR",
                "nullable": false,
                "precision": 4
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
                "index": 1,
                "name": "$1",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 4,
                "name": "$4",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "account",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "district",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}