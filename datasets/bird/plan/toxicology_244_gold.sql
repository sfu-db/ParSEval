{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 5,
      "name": "$5",
      "type": "VARCHAR"
    }
  ],
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "IN",
            "operator": "IN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 5,
                "name": "$5",
                "type": "VARCHAR"
              }
            ],
            "query": [
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
                          "index": 5,
                          "name": "$5",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "LITERAL",
                          "value": "p",
                          "type": "VARCHAR",
                          "nullable": false,
                          "precision": -1
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
                              "index": 3,
                              "name": "$3",
                              "type": "VARCHAR"
                            }
                          ]
                        },
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "connected",
                            "id": "0",
                            "inputs": []
                          },
                          {
                            "relOp": "LogicalTableScan",
                            "table": "atom",
                            "id": "1",
                            "inputs": []
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
            "kind": "EQUALS",
            "operator": "=",
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
                "value": "n",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "8",
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
                "index": 3,
                "name": "$3",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "7",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "atom",
              "id": "5",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "connected",
              "id": "6",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}