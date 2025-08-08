{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 58,
      "name": "$58",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 17,
      "name": "$17",
      "type": "VARCHAR"
    },
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
                  "index": 20,
                  "name": "$20",
                  "type": "DATE"
                }
              ]
            }
          ]
        }
      ]
    },
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
                  "index": 21,
                  "name": "$21",
                  "type": "DATE"
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
            "kind": "GREATER_THAN",
            "operator": ">",
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
                            "index": 20,
                            "name": "$20",
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
                "value": "1991",
                "type": "CHAR",
                "nullable": false,
                "precision": 4
              }
            ]
          },
          {
            "kind": "LESS_THAN",
            "operator": "<",
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
                            "index": 21,
                            "name": "$21",
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
          "joinType": "left",
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
                "index": 49,
                "name": "$49",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "schools",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "satscores",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}